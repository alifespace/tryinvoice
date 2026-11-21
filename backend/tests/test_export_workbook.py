import re
from datetime import date
from decimal import Decimal
from io import BytesIO
from uuid import NAMESPACE_URL, UUID, uuid5

from openpyxl import load_workbook

from app.exporting.workbook import build_workbook
from app.rules.seed import load_cmi_default
from app.transactions.query import TransactionDocumentOut, TransactionOut


def _id(value: str) -> UUID:
    return uuid5(NAMESPACE_URL, value)


def _tx(
    name: str,
    *,
    category: str = "EAT",
    scope: str = "in_scope",
    detail: str = "美洲",
    currency: str = "USD",
    amount: str = "10.00",
    claim: str = "10.00",
    status: str = "paired",
    notes: str | None = None,
    flags: list[str] | None = None,
    documents: list[TransactionDocumentOut] | None = None,
) -> TransactionOut:
    return TransactionOut(
        id=_id(name),
        category=category,
        merchant=name,
        tx_date=date(2026, 10, 4),
        region_scope=scope,
        region_detail=detail,
        currency=currency,
        original_amount=Decimal(amount),
        claim_amount=Decimal(claim),
        tip_amount=Decimal("0.00"),
        split_count=1,
        payment_status=status,
        airport_direction=None,
        airport_iata=None,
        flags=flags or [],
        notes=notes if notes is not None else f"note-{name}",
        is_user_verified=False,
        documents=documents or [],
    )


def _doc(name: str, role: str) -> TransactionDocumentOut:
    return TransactionDocumentOut(
        id=_id(name), role=role, original_filename=f"{name}.pdf"
    )


def _evaluate(sheet, cell: str) -> Decimal:
    value = sheet[cell].value
    if not isinstance(value, str) or not value.startswith("="):
        return Decimal(str(value or 0))
    formula = value[1:]
    total = re.fullmatch(r"SUM\(([IJ])(\d+):\1(\d+)\)", formula)
    if total:
        column, first, last = total.groups()
        return sum(
            (
                _evaluate(sheet, f"{column}{row}")
                for row in range(int(first), int(last) + 1)
            ),
            Decimal(0),
        )
    references = re.findall(r"[IJ]\d+", formula)
    return sum((_evaluate(sheet, ref) for ref in references), Decimal(0))


def _load(txs, names, alerts=(), rules=None):
    data = build_workbook(txs, names, rules or load_cmi_default(), alerts)
    return load_workbook(BytesIO(data))


def test_workbook_groups_sums_by_currency_and_marks_rows() -> None:
    invoice = _doc("invoice", "invoice")
    receipt = _doc("receipt", "receipt")
    highlight_flag = "TIP\x0cEXCLUDED"
    rules = load_cmi_default()
    rules.compliance_rules.exclude_ride_hailing_tips.highlight_flag = highlight_flag
    txs = [
        _tx(
            "cafe",
            detail="Synthetic\x0cregion",
            amount="30.50",
            claim="27.00",
            notes="before\x0cafter\nnext line",
            flags=[highlight_flag, "MEAL_SPLIT"],
            documents=[receipt, invoice],
        ),
        _tx(
            "transit",
            category="TRANS",
            amount="0.00",
            claim="0.00",
            status="exempted_transit",
        ),
        _tx("missing-pay", amount="10.00", status="missing_payment"),
        _tx(
            "china",
            category="Others",
            scope="out_of_scope",
            detail="大陆",
            currency="CNY",
            amount="20.00",
            claim="20.00",
        ),
        _tx(
            "unknown",
            category="UNKNOWN",
            scope="out_of_scope",
            detail="大陆",
            currency="CNY",
            amount="2.00",
            claim="2.00",
            status="missing_invoice",
        ),
    ]
    names = {
        invoice.id: "cafe_invoice.pdf",
        receipt.id: "cafe_receipt.pdf",
    }

    workbook = _load(
        txs,
        names,
        [
            {
                "direction": "To",
                "iata": "LAX",
                "expected_date": "2026-10-05",
                "code": "MISSING_AIRPORT_TRANSFER",
            }
        ],
        rules=rules,
    )
    sheet = workbook["明细"]

    assert "|".join(cell.value for cell in sheet[1]) == (
        "报销分类|区域范围|地域|原始文件名|重命名后文件名|交易日期|"
        "商户/事由|币种|原单金额|报销金额|付款凭证状态|备注"
    )
    detail_rows = {
        sheet.cell(row, 7).value: row
        for row in range(2, sheet.max_row + 1)
        if sheet.cell(row, 7).value
        in {"cafe", "missing-pay", "china", "unknown", "transit"}
    }
    cafe_row = detail_rows["cafe"]
    assert sheet.cell(cafe_row, 1).value == "餐费-正餐"
    assert sheet.cell(cafe_row, 2).value == "范围内"
    assert sheet.cell(cafe_row, 4).value == "invoice.pdf\nreceipt.pdf"
    assert sheet.cell(cafe_row, 5).value == "cafe_invoice.pdf\ncafe_receipt.pdf"
    assert sheet.cell(cafe_row, 5).hyperlink.target == "cafe_invoice.pdf"
    assert sheet.cell(cafe_row, 9).value == Decimal("30.50")
    assert sheet.cell(cafe_row, 5).fill.fgColor.rgb.endswith("FFF2CC")
    assert sheet.cell(cafe_row, 12).value == "beforeafter\nnext line"
    assert (
        sheet.cell(cafe_row, 12).comment.text
        == "触发原因：规则标记：TIPEXCLUDED；多人用餐分摊"
    )
    assert "Syntheticregion / USD" in [
        sheet.cell(row, 1).value for row in range(2, sheet.max_row + 1)
    ]
    transit_row = detail_rows["transit"]
    assert sheet.cell(transit_row, 11).value == "免付款凭证（公共交通）"
    missing_row = detail_rows["missing-pay"]
    assert sheet.cell(missing_row, 11).value == "[待确认-缺付款凭证]"
    assert sheet.cell(missing_row, 1).fill.fgColor.rgb.endswith("FFC7CE")
    assert "缺付款凭证" in sheet.cell(missing_row, 12).comment.text
    assert sheet.cell(detail_rows["china"], 2).value == "范围外"
    assert sheet.cell(detail_rows["unknown"], 1).value == "待分类"
    assert sheet.cell(detail_rows["unknown"], 1).fill.fgColor.rgb.endswith("FFC7CE")
    assert sheet.cell(detail_rows["china"], 12).comment is None

    totals = {
        sheet.cell(row, 8).value: row
        for row in range(2, sheet.max_row + 1)
        if sheet.cell(row, 7).value == "合计"
    }
    assert set(totals) == {"USD", "CNY"}
    assert _evaluate(sheet, f"I{totals['USD']}") == Decimal("40.50")
    assert _evaluate(sheet, f"J{totals['USD']}") == Decimal("37.00")
    assert _evaluate(sheet, f"I{totals['CNY']}") == Decimal("22.00")
    assert _evaluate(sheet, f"J{totals['CNY']}") == Decimal("22.00")
    formulas = [
        cell.value
        for row in sheet.iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("=")
    ]
    assert any(str(formula).startswith("=SUM(") for formula in formulas) and all(
        "#REF!" not in str(value) and "#VALUE!" not in str(value) for value in formulas
    )

    alerts = workbook["疑似漏交清单"]
    assert "|".join(cell.value for cell in alerts[1]) == "方向|机场|预期日期|说明"
    assert "|".join(cell.value for cell in alerts[2]) == (
        "To|LAX|2026-10-05|MISSING_AIRPORT_TRANSFER"
    )


def test_empty_workbook_has_headers_and_no_alert_message() -> None:
    workbook = _load([], {}, [])

    assert workbook["明细"].max_row == 1
    assert workbook["明细"].max_column == 12
    assert workbook["疑似漏交清单"]["A2"].value == "无疑似漏交"


def test_workbook_has_only_group_subtotals_and_references_each_level() -> None:
    workbook = _load([_tx("cafe")], {})
    sheet = workbook["明细"]

    titles = [
        (row, sheet.cell(row, 1).value)
        for row in range(2, sheet.max_row + 1)
        if sheet.cell(row, 1).value in {"餐费", "范围内", "美洲 / USD"}
    ]
    assert [title for _, title in titles] == ["餐费", "范围内", "美洲 / USD"]

    subtotals = [
        row for row in range(2, sheet.max_row + 1) if sheet.cell(row, 7).value == "小计"
    ]
    assert len(subtotals) == 3
    detail_row = next(
        row for row in range(2, sheet.max_row + 1) if sheet.cell(row, 7).value == "cafe"
    )
    total_row = next(
        row for row in range(2, sheet.max_row + 1) if sheet.cell(row, 7).value == "合计"
    )
    assert sheet.cell(subtotals[0], 9).value == f"=SUM(I{detail_row}:I{detail_row})"
    assert sheet.cell(subtotals[1], 9).value == f"=I{subtotals[0]}"
    assert sheet.cell(subtotals[2], 9).value == f"=I{subtotals[1]}"
    assert sheet.cell(total_row, 9).value == f"=I{subtotals[2]}"
    assert _evaluate(sheet, f"I{total_row}") == Decimal("10.00")


def test_external_text_is_written_as_literal_text() -> None:
    document = _doc("formula-name", "invoice")
    transaction = _tx(
        "=1+1",
        notes="#VALUE!",
        documents=[document],
    )
    workbook = _load(
        [transaction],
        {document.id: "=2+2.pdf"},
        [{"direction": "=3+3", "description": "#VALUE!"}],
    )
    sheet = workbook["明细"]
    detail_row = next(
        row for row in range(2, sheet.max_row + 1) if sheet.cell(row, 7).value == "=1+1"
    )

    for column, expected in (
        (4, "formula-name.pdf"),
        (5, "=2+2.pdf"),
        (7, "=1+1"),
        (12, "#VALUE!"),
    ):
        cell = sheet.cell(detail_row, column)
        assert cell.value == expected
        assert cell.data_type == "s"

    alert = workbook["疑似漏交清单"]["A2"]
    description = workbook["疑似漏交清单"]["D2"]
    assert alert.value == "=3+3"
    assert alert.data_type == "s"
    assert description.value == "#VALUE!"
    assert description.data_type == "s"
