from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from itertools import groupby
from typing import Any
from uuid import UUID

from openpyxl import Workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.rules.dsl import RuleTemplateDSL
from app.transactions.query import TransactionOut

_HEADERS = (
    "报销分类|区域范围|地域|原始文件名|重命名后文件名|交易日期|"
    "商户/事由|币种|原单金额|报销金额|付款凭证状态|备注"
).split("|")
_PAYMENT_LABELS = {
    "paired": "已配对",
    "missing_payment": "[待确认-缺付款凭证]",
    "missing_invoice": "[待确认-缺Invoice]",
    "exempted_transit": "免付款凭证（公共交通）",
}
_RED_FILL = PatternFill("solid", fgColor="FFC7CE")
_YELLOW_FILL = PatternFill("solid", fgColor="FFF2CC")
_GROUP_FILL = PatternFill("solid", fgColor="D9EAF7")


@dataclass(frozen=True, slots=True)
class _ExportRow:
    transaction: TransactionOut
    group_keys: tuple[tuple[str, ...], ...]
    group_labels: tuple[str, ...]


def _group_value(
    transaction: TransactionOut, level: str, rules: RuleTemplateDSL
) -> tuple[tuple[str, ...], str]:
    if level == "category_group":
        category_groups = {item.code: item.group for item in rules.categories}
        value = category_groups.get(transaction.category, transaction.category)
        return (value,), value
    if level == "region_scope":
        hierarchy = rules.region_hierarchy
        scope_labels = {
            "in_scope": hierarchy.in_scope.label,
            "out_of_scope": hierarchy.out_of_scope.label,
        }
        rank = "0" if transaction.region_scope == "in_scope" else "1"
        label = scope_labels.get(transaction.region_scope, transaction.region_scope)
        return (rank, label), label
    return (
        (transaction.region_detail, transaction.currency),
        f"{transaction.region_detail} / {transaction.currency}",
    )


def _export_rows(
    txs: Sequence[TransactionOut], rules: RuleTemplateDSL
) -> list[_ExportRow]:
    levels = rules.naming_and_export.excel_group_levels
    rows = []
    for transaction in txs:
        groups = tuple(_group_value(transaction, level, rules) for level in levels)
        rows.append(
            _ExportRow(
                transaction,
                tuple(group[0] for group in groups),
                tuple(group[1] for group in groups),
            )
        )
    return sorted(
        rows,
        key=lambda row: (
            tuple(key for group in row.group_keys for key in group),
            row.transaction.currency,
            row.transaction.tx_date,
            row.transaction.merchant,
            str(row.transaction.id),
        ),
    )


def _highlight_reason(
    transaction: TransactionOut, rules: RuleTemplateDSL
) -> tuple[str | None, str]:
    red_reasons = []
    if transaction.payment_status == "missing_payment":
        red_reasons.append("缺付款凭证")
    if transaction.payment_status == "missing_invoice":
        red_reasons.append("缺 Invoice")
    if transaction.category == "UNKNOWN":
        red_reasons.append("分类为 UNKNOWN")
    if red_reasons:
        return "FFC7CE", "；".join(red_reasons)
    yellow_reasons = []
    tip_flag = rules.compliance_rules.exclude_ride_hailing_tips.highlight_flag
    if tip_flag and tip_flag in transaction.flags:
        yellow_reasons.append(f"规则标记：{tip_flag}")
    if "MEAL_SPLIT" in transaction.flags:
        yellow_reasons.append("多人用餐分摊")
    return ("FFF2CC", "；".join(yellow_reasons)) if yellow_reasons else (None, "")


def _set_literal_text(
    cells: Sequence[Cell | MergedCell], values: Sequence[object]
) -> None:
    for cell, value in zip(cells, values, strict=True):
        if isinstance(value, str):
            cell.data_type = "s"


def _xlsx_safe_text(value: str) -> str:
    return "".join(
        character
        for character in value
        if character in "\t\n\r"
        or "\x20" <= character <= "\ud7ff"
        or "\ue000" <= character <= "\ufffd"
        or "\U00010000" <= character <= "\U0010ffff"
    )


def _xlsx_safe_values(values: Sequence[object]) -> tuple[Any, ...]:
    return tuple(
        _xlsx_safe_text(value) if isinstance(value, str) else value for value in values
    )


def _write_detail(
    sheet: Worksheet,
    row: _ExportRow,
    names: Mapping[UUID, str],
    rules: RuleTemplateDSL,
) -> None:
    transaction = row.transaction
    categories = {item.code: item.label for item in rules.categories}
    scopes = {
        "in_scope": rules.region_hierarchy.in_scope.label,
        "out_of_scope": rules.region_hierarchy.out_of_scope.label,
    }
    documents = sorted(
        transaction.documents, key=lambda item: (item.role != "invoice", item.id)
    )
    original_names = "\n".join(document.original_filename for document in documents)
    renamed = [names[document.id] for document in documents if document.id in names]
    values = (
        categories.get(transaction.category, transaction.category),
        scopes.get(transaction.region_scope, transaction.region_scope),
        transaction.region_detail,
        original_names,
        "\n".join(renamed),
        transaction.tx_date,
        transaction.merchant,
        transaction.currency,
        transaction.original_amount,
        transaction.claim_amount,
        _PAYMENT_LABELS.get(transaction.payment_status, transaction.payment_status),
        transaction.notes,
    )
    safe_values = _xlsx_safe_values(values)
    sheet.append(safe_values)
    cells = sheet[sheet.max_row]
    _set_literal_text(cells, safe_values)
    cells[8].number_format = "#,##0.00"
    cells[9].number_format = "#,##0.00"
    if renamed:
        cells[4].hyperlink = _xlsx_safe_text(renamed[0])
    fill, reason = _highlight_reason(transaction, rules)
    if fill:
        row_fill = _RED_FILL if fill == "FFC7CE" else _YELLOW_FILL
        for cell in cells:
            cell.fill = row_fill
        cells[11].comment = Comment(
            _xlsx_safe_text(f"触发原因：{reason}"), "TryInvoice"
        )


def _write_leaf(
    worksheet: Worksheet,
    rows: Sequence[_ExportRow],
    names: Mapping[UUID, str],
    rules: RuleTemplateDSL,
) -> dict[str, list[int]]:
    references: dict[str, list[int]] = defaultdict(list)
    for currency, group in groupby(rows, key=lambda item: item.transaction.currency):
        start = worksheet.max_row + 1
        for row in group:
            _write_detail(worksheet, row, names, rules)
        end = worksheet.max_row
        subtotal = worksheet.max_row + 1
        worksheet.cell(subtotal, 7, "小计").data_type = "s"
        worksheet.cell(subtotal, 8, currency).data_type = "s"
        for column in (9, 10):
            letter = get_column_letter(column)
            cell = worksheet.cell(
                subtotal, column, f"=SUM({letter}{start}:{letter}{end})"
            )
            cell.number_format = "#,##0.00"
        _style_total(worksheet, subtotal)
        references[currency].append(subtotal)
    return dict(references)


def _style_total(worksheet: Worksheet, row: int) -> None:
    for cell in worksheet[row]:
        cell.font = Font(bold=True)


def _write_group(
    worksheet: Worksheet,
    rows: Sequence[_ExportRow],
    level: int,
    names: Mapping[UUID, str],
    rules: RuleTemplateDSL,
) -> dict[str, list[int]]:
    levels = rules.naming_and_export.excel_group_levels
    if level == len(levels):
        return _write_leaf(worksheet, rows, names, rules)
    child_references: dict[str, list[int]] = defaultdict(list)
    for _, grouped in groupby(rows, key=lambda item: item.group_keys[level]):
        child_rows = tuple(grouped)
        title_row = worksheet.max_row + 1
        title = child_rows[0].group_labels[level]
        worksheet.cell(title_row, 1, _xlsx_safe_text(title)).data_type = "s"
        for cell in worksheet[title_row]:
            cell.fill = _GROUP_FILL
            cell.font = Font(bold=True)
        references = _write_group(worksheet, child_rows, level + 1, names, rules)
        for currency, rows_for_currency in references.items():
            child_references[currency].extend(rows_for_currency)
    if level == 0:
        return dict(child_references)
    return _write_formula_totals(worksheet, child_references, "小计")


def _write_formula_totals(
    worksheet: Worksheet, references: Mapping[str, Sequence[int]], label: str
) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for currency in sorted(references):
        subtotal = worksheet.max_row + 1
        worksheet.cell(subtotal, 7, label).data_type = "s"
        worksheet.cell(subtotal, 8, currency).data_type = "s"
        for column in (9, 10):
            letter = get_column_letter(column)
            refs = "+".join(f"{letter}{row}" for row in references[currency])
            cell = worksheet.cell(subtotal, column, f"={refs}")
            cell.number_format = "#,##0.00"
        _style_total(worksheet, subtotal)
        result[currency] = [subtotal]
    return result


def _write_alerts(workbook: Workbook, alerts: Sequence[Mapping[str, str]]) -> None:
    sheet = workbook.create_sheet("疑似漏交清单")
    sheet.append(("方向", "机场", "预期日期", "说明"))
    for alert in alerts:
        values = (
            alert.get("direction", ""),
            alert.get("iata", ""),
            alert.get("expected_date", ""),
            alert.get("description", alert.get("code", "")),
        )
        safe_values = _xlsx_safe_values(values)
        sheet.append(safe_values)
        _set_literal_text(sheet[sheet.max_row], safe_values)
    if not alerts:
        sheet.append(("无疑似漏交",))
    for cell in sheet[1]:
        cell.font = Font(bold=True)


def build_workbook(
    txs: Sequence[TransactionOut],
    names: Mapping[UUID, str],
    rules: RuleTemplateDSL,
    missing_alerts: Sequence[Mapping[str, str]],
) -> bytes:
    workbook = Workbook()
    sheet = workbook["Sheet"]
    sheet.title = "明细"
    sheet.append(_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    rows = _export_rows(txs, rules)
    if rows:
        references = _write_group(sheet, rows, 0, names, rules)
        _write_formula_totals(sheet, references, "合计")
    _write_alerts(workbook, missing_alerts)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
