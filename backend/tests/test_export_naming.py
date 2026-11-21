from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from app.exporting.naming import amount_hyphen, excel_filename, plan_voucher_names
from app.rules.seed import load_cmi_default
from app.transactions.query import TransactionDocumentOut, TransactionOut


def _id(value: str) -> UUID:
    return uuid5(NAMESPACE_URL, value)


def _transaction(
    transaction_id: str,
    category: str = "EAT",
    documents: list[TransactionDocumentOut] | None = None,
    **updates: object,
) -> TransactionOut:
    values: dict[str, object] = {
        "id": _id(transaction_id),
        "category": category,
        "merchant": "Cafe",
        "tx_date": date(2026, 10, 4),
        "region_scope": "in_scope",
        "region_detail": "美洲",
        "currency": "USD",
        "original_amount": Decimal("21.23"),
        "claim_amount": Decimal("21.23"),
        "tip_amount": Decimal("0.00"),
        "split_count": 1,
        "payment_status": "paired",
        "airport_direction": None,
        "airport_iata": None,
        "flags": [],
        "notes": "",
        "is_user_verified": False,
        "documents": documents or [],
    }
    values.update(updates)
    return TransactionOut(**values)


def _document(value: str, role: str = "receipt") -> TransactionDocumentOut:
    return TransactionDocumentOut(
        id=_id(value), role=role, original_filename=f"{value}.pdf"
    )


def test_amount_hyphen() -> None:
    assert [amount_hyphen(Decimal(value)) for value in ("21.23", "100.9", "1.235")] == [
        "21-23",
        "100-90",
        "1-24",
    ]
    with pytest.raises(ValueError):
        amount_hyphen(Decimal("-0.01"))


def test_voucher_names_choose_airport_pattern_and_resolve_collisions() -> None:
    first_doc = _document("first")
    second_doc = _document("second")
    airport_doc = _document("airport", "invoice")
    first = _transaction("first-tx", documents=[first_doc])
    second = _transaction("second-tx", documents=[second_doc])
    airport = _transaction(
        "airport-tx",
        category="TRANS",
        documents=[airport_doc],
        airport_direction="To",
        airport_iata="SEA",
    )
    extensions = {
        first_doc.id: "pdf",
        second_doc.id: "pdf",
        airport_doc.id: "jpg",
    }

    naming = load_cmi_default().naming_and_export
    names = plan_voucher_names([second, airport, first], extensions, naming)

    earlier, later = sorted((first, second), key=lambda transaction: transaction.id)
    assert names[earlier.documents[0].id] == "EAT_20261004_USD_21-23_receipt.pdf"
    assert names[later.documents[0].id] == "EAT_20261004_USD_21-23_2_receipt.pdf"
    assert names[airport_doc.id] == "TRANS_ToSEA_20261004_USD_21-23_invoice.jpg"
    literal_suffix_names = plan_voucher_names(
        [second, first],
        extensions,
        naming.model_copy(
            update={"standard_pattern": "{category}{role_suffix}_receipt.{ext}"}
        ),
    )
    assert literal_suffix_names[earlier.documents[0].id] == "EAT_receipt_receipt.pdf"
    assert literal_suffix_names[later.documents[0].id] == "EAT_2_receipt_receipt.pdf"
    simple_names = plan_voucher_names(
        [first, second, _transaction("extra", "EAT_2", [airport_doc])],
        extensions | {airport_doc.id: "pdf"},
        naming.model_copy(update={"standard_pattern": "{category}.{ext}"}),
    )
    assert set(simple_names.values()) == {"EAT.pdf", "EAT_2.pdf", "EAT_2_2.pdf"}
    document = _document("missing")
    with pytest.raises(ValueError, match="缺少文档扩展名"):
        plan_voucher_names(
            [_transaction("tx", documents=[document])],
            {},
            naming,
        )
    assert (
        excel_filename(
            load_cmi_default().naming_and_export,
            '出差/报销:*?"<>|\\\x01\x85',
            date(2026, 11, 21),
        )
        == "出差报销汇总_2026-11-21.xlsx"
    )
