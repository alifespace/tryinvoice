import json
from datetime import date
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from uuid import NAMESPACE_URL, UUID, uuid5
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from app.exporting.bundle import (
    build_export_zip,
    build_manifest,
    export_zip_name,
    transaction_fingerprint,
)
from app.exporting.naming import plan_voucher_names
from app.exporting.workbook import build_workbook
from app.rules.seed import load_cmi_default
from app.transactions.query import TransactionDocumentOut, TransactionOut


def _id(value: str) -> UUID:
    return uuid5(NAMESPACE_URL, value)


def _transaction(name: str, filename: str) -> TransactionOut:
    document = TransactionDocumentOut(
        id=_id(f"{name}-doc"), role="receipt", original_filename=filename
    )
    return TransactionOut(
        id=_id(f"{name}-tx"),
        category="EAT",
        merchant="Cafe",
        tx_date=date(2026, 10, 4),
        region_scope="in_scope",
        region_detail="美洲",
        currency="USD",
        original_amount=Decimal("21.23"),
        claim_amount=Decimal("21.23"),
        tip_amount=Decimal("0.00"),
        split_count=1,
        payment_status="paired",
        airport_direction=None,
        airport_iata=None,
        flags=[],
        notes="private note",
        is_user_verified=False,
        documents=[document],
    )


def test_manifest_fingerprint_and_missing_document_mapping() -> None:
    first, second = (
        _transaction("first", "first-收据.pdf"),
        _transaction("second", "second.pdf"),
    )
    names = {first.documents[0].id: "a.pdf", second.documents[0].id: "b.pdf"}
    hashes = {
        first.documents[0].id: "a" * 64,
        second.documents[0].id: "b" * 64,
    }
    result = build_manifest(
        _id("batch"), date(2026, 10, 4), [second, first], names, hashes
    )
    manifest = json.loads(result)
    fingerprint = sha256(b"EAT|2026-10-04|USD|21-23|Cafe").hexdigest()
    assert manifest == {
        "version": 1,
        "batch_id": str(_id("batch")),
        "batch_date": "2026-10-04",
        "entries": [
            {
                "document_id": str(first.documents[0].id),
                "transaction_id": str(first.id),
                "fingerprint": fingerprint,
                "original_filename": "first-收据.pdf",
                "sha256": "a" * 64,
                "export_filename": "a.pdf",
            },
            {
                "document_id": str(second.documents[0].id),
                "transaction_id": str(second.id),
                "fingerprint": fingerprint,
                "original_filename": "second.pdf",
                "sha256": "b" * 64,
                "export_filename": "b.pdf",
            },
        ],
    }
    expected_json = (
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    )
    assert result == expected_json.encode()
    assert result.endswith(b"\n")
    assert "收据" in result.decode("utf-8")
    assert "private note" not in result.decode()
    assert transaction_fingerprint(first) == fingerprint
    assert export_zip_name(_id("batch"), date(2026, 10, 4)).startswith(
        "export_2026-10-04_"
    )


@pytest.mark.parametrize(
    "missing_mapping",
    ["name", "sha256"],
)
def test_manifest_rejects_each_missing_document_mapping(
    missing_mapping: str,
) -> None:
    tx = _transaction("missing", "missing.pdf")
    doc_id = tx.documents[0].id
    names = {} if missing_mapping == "name" else {doc_id: "missing.pdf"}
    hashes = {} if missing_mapping == "sha256" else {doc_id: "a" * 64}
    with pytest.raises(ValueError):
        build_manifest(_id("batch"), date(2026, 10, 4), [tx], names, hashes)


def test_zip_is_flat_valid_deterministic_and_workbook_links_resolve() -> None:
    txs = [_transaction("one", "receipt.pdf"), _transaction("two", "receipt.pdf")]
    document_ids = [tx.documents[0].id for tx in txs]
    rules = load_cmi_default()
    names = plan_voucher_names(
        txs, {doc_id: "pdf" for doc_id in document_ids}, rules.naming_and_export
    )
    workbook = build_workbook(txs, names, rules, [])
    vouchers = {
        names[doc_id]: f"voucher-{index}".encode()
        for index, doc_id in enumerate(document_ids)
    }
    manifest = build_manifest(
        _id("batch"),
        date(2026, 10, 4),
        txs,
        names,
        {doc_id: "b" * 64 for doc_id in document_ids},
    )
    payload = build_export_zip("report.xlsx", workbook, vouchers, manifest)
    assert payload == build_export_zip("report.xlsx", workbook, vouchers, manifest)
    with ZipFile(BytesIO(payload)) as archive:
        members = archive.namelist()
        assert members == [
            "report.xlsx",
            *sorted(vouchers),
            ".processed_manifest.json",
        ]
        assert archive.testzip() is None
        assert all(
            info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()
        )
        assert archive.read("report.xlsx") == workbook
        assert archive.read(".processed_manifest.json") == manifest
        for name, data in vouchers.items():
            assert archive.read(name) == data
        sheet = load_workbook(BytesIO(archive.read("report.xlsx")))["明细"]
        assert sheet.max_column == 12
        assert [sheet.cell(1, column).value for column in range(1, 13)] == [
            "报销分类",
            "区域范围",
            "地域",
            "原始文件名",
            "重命名后文件名",
            "交易日期",
            "商户/事由",
            "币种",
            "原单金额",
            "报销金额",
            "付款凭证状态",
            "备注",
        ]
        targets = {
            cell.hyperlink.target
            for row in sheet.iter_rows()
            for cell in row
            if cell.hyperlink is not None
        }
        assert targets and targets <= set(vouchers)


@pytest.mark.parametrize("name", ["", "../x", "a/b", "a\\b", "a\x00b"])
def test_zip_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(ValueError):
        build_export_zip("report.xlsx", b"x", {name: b"y"}, b"{}")


@pytest.mark.parametrize(
    ("workbook_name", "vouchers"),
    [
        ("same.xlsx", {"same.xlsx": b"x"}),
        (".processed_manifest.json", {}),
    ],
)
def test_zip_rejects_duplicate_reserved_names(
    workbook_name: str, vouchers: dict[str, bytes]
) -> None:
    with pytest.raises(ValueError):
        build_export_zip(workbook_name, b"x", vouchers, b"{}")
