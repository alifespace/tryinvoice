from datetime import date
from decimal import Decimal

import pytest

from app.extraction.models import ExtractedDocument
from app.rules.documents import PartitionResult, RuleDocument, partition_documents
from app.rules.pairing import PairedEntry, pair_documents
from app.rules.seed import load_cmi_default

_DAY = date(2026, 10, 9)


def _rules(**updates: object):
    return load_cmi_default().compliance_rules.model_copy(update=updates)


def _doc(
    doc_id: str,
    role: str,
    *,
    merchant: str | None = "Coffee House",
    amount: Decimal | None = Decimal("25.00"),
    currency: str | None = "USD",
    day: date | None = _DAY,
    card: str | None = None,
) -> RuleDocument:
    return RuleDocument(
        doc_id,
        ExtractedDocument(
            doc_role=role,
            merchant=merchant,
            original_amount=amount,
            currency=currency,
            occurred_on=day,
            card_last4=card,
            confidence=0.8,
            source="client_ocr",
        ),
    )


def _partition(
    invoices: tuple[RuleDocument, ...] = (),
    receipts: tuple[RuleDocument, ...] = (),
) -> PartitionResult:
    return PartitionResult(invoices, receipts, (), (), (), {})


def _pair(
    invoices: tuple[RuleDocument, ...],
    receipts: tuple[RuleDocument, ...],
    **rules: object,
):
    return pair_documents(_partition(invoices, receipts), _rules(**rules))


def test_matching_invoice_and_receipt_contribute_one_logical_entry() -> None:
    invoice, receipt = _doc("inv", "invoice"), _doc("pay", "payment_receipt")
    entries = _pair((invoice,), (receipt,))
    assert len(entries) == 1
    assert entries[0] == PairedEntry(invoice, receipt, "paired")


def test_paired_entry_requires_at_least_one_document() -> None:
    with pytest.raises(ValueError, match="至少需要一张凭证"):
        PairedEntry(None, None, "paired")


@pytest.mark.parametrize(("delta", "matched"), [(1, True), (2, False)])
def test_date_tolerance_is_inclusive(delta: int, matched: bool) -> None:
    entries = _pair(
        (_doc("inv", "invoice"),),
        (
            _doc(
                "pay", "payment_receipt", day=date.fromordinal(_DAY.toordinal() + delta)
            ),
        ),
    )
    assert len(entries) == (1 if matched else 2)
    assert entries[0].payment_status == ("paired" if matched else "missing_payment")
    if not matched:
        assert entries[1].payment_status == "missing_invoice"


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "HKD"},
        {"amount": Decimal("26.00")},
        {"merchant": "Unrelated Bakery"},
        {"currency": None},
        {"amount": None},
        {"day": None},
        {"day": date(2026, 10, 12)},
    ],
)
def test_incompatible_candidates_remain_unpaired(changes: dict[str, object]) -> None:
    entries = _pair(
        (_doc("inv", "invoice"),),
        (_doc("pay", "payment_receipt", **changes),),  # type: ignore[arg-type]
    )
    assert tuple(entry.payment_status for entry in entries) == (
        "missing_payment",
        "missing_invoice",
    )


def test_different_known_card_suffixes_do_not_pair() -> None:
    entries = _pair(
        (_doc("inv", "invoice", card="5678"),),
        (_doc("pay", "payment_receipt", card="1234"),),
    )
    assert tuple(entry.payment_status for entry in entries) == (
        "missing_payment",
        "missing_invoice",
    )


def test_missing_merchant_is_compatible_and_primary_supports_receipt_only() -> None:
    invoice = _doc("inv", "invoice", merchant=None)
    receipt = _doc("pay", "payment_receipt", merchant="Coffee House")
    paired = _pair((invoice,), (receipt,))
    assert paired[0].payment_status == "paired"
    single = _pair((), (receipt,))[0]
    assert single.primary is receipt


def test_greedy_assignment_prefers_merchant_similarity_and_is_one_to_one() -> None:
    invoices = (
        _doc("alpha-inv", "invoice", merchant="Alpha Medical Center"),
        _doc("beta-inv", "invoice", merchant="Beta Coffee Shop"),
    )
    receipts = (
        _doc("beta-pay", "payment_receipt", merchant="Beta Coffee Shop Card"),
        _doc("alpha-pay", "payment_receipt", merchant="Alpha Medical Center Card"),
    )
    for ordered_invoices, ordered_receipts in (
        (invoices, receipts),
        (tuple(reversed(invoices)), tuple(reversed(receipts))),
    ):
        entries = _pair(ordered_invoices, ordered_receipts)
        pairs = {(entry.invoice.doc_id, entry.receipt.doc_id) for entry in entries}
        assert pairs == {("alpha-inv", "alpha-pay"), ("beta-inv", "beta-pay")}

    card_match = _pair(
        (_doc("inv", "invoice", card="4242"),),
        (
            _doc("unknown-card", "payment_receipt"),
            _doc("same-card", "payment_receipt", card="4242"),
        ),
    )
    matched = next(entry for entry in card_match if entry.invoice)
    assert matched.receipt.doc_id == "same-card"
    assert sum(entry.payment_status == "missing_invoice" for entry in card_match) == 1


@pytest.mark.parametrize(
    ("merchant", "currency", "card", "exemptions", "kind"),
    [
        ("MTR Corporation", "HKD", None, ["octopus_mtr"], "octopus_mtr"),
        ("港铁", "HKD", None, ["octopus_mtr"], "octopus_mtr"),
        ("Octopus Tea House", "HKD", None, ["octopus_mtr"], None),
        ("MTR Corporation", "USD", None, ["octopus_mtr"], None),
        (
            "MTA NYCT",
            "USD",
            "4242",
            ["credit_card_public_transit"],
            "credit_card_public_transit",
        ),
        ("MTA", "USD", None, ["credit_card_public_transit"], None),
        ("Subway Sandwiches", "USD", "4242", ["credit_card_public_transit"], None),
        ("MTA", "USD", "4242", [], None),
        (
            "地铁 MTA",
            "HKD",
            "4242",
            ["octopus_mtr", "credit_card_public_transit"],
            "octopus_mtr",
        ),
    ],
)
def test_unpaired_transit_receipt_exemptions(
    merchant: str,
    currency: str,
    card: str | None,
    exemptions: list[str],
    kind: str | None,
) -> None:
    entry = _pair(
        (),
        (
            _doc(
                "pay",
                "payment_receipt",
                merchant=merchant,
                currency=currency,
                card=card,
            ),
        ),
        transit_direct_pay_exemptions=exemptions,
    )[0]
    assert entry.payment_status == (
        "exempted_transit" if kind is not None else "missing_invoice"
    )
    assert entry.transit_kind == kind
    for status in ("paired", "missing_payment", "missing_invoice"):
        with pytest.raises(ValueError, match="交通类型"):
            PairedEntry(None, entry.receipt, status, "octopus_mtr")


def test_paired_transit_receipt_stays_paired() -> None:
    entries = _pair(
        (_doc("inv", "invoice", merchant="MTA"),),
        (_doc("pay", "payment_receipt", merchant="MTA", card="4242"),),
    )
    assert len(entries) == 1
    assert entries[0].payment_status == "paired"
    assert entries[0].transit_kind is None


def test_closure_switch_accepts_unmatched_documents_without_transit_label() -> None:
    invoice, receipt = (
        _doc("inv", "invoice"),
        _doc("pay", "payment_receipt", merchant="MTR", currency="HKD"),
    )
    entries = _pair((invoice,), (receipt,), require_invoice_payment_closure=False)
    assert tuple(entry.payment_status for entry in entries) == ("paired", "paired")
    assert all(entry.transit_kind is None for entry in entries)


def test_partition_integration_ignores_other_roles_and_preserves_input() -> None:
    documents = (
        _doc("inv", "invoice"),
        _doc("pay", "payment_receipt"),
        _doc("trip", "trip_application"),
        _doc("other", "unknown"),
    )
    partition = partition_documents(
        documents, _rules(deduplicate_reshot_receipts=False)
    )
    before = (partition.invoices, partition.receipts, partition.trip_applications)
    entries = pair_documents(partition, _rules())
    assert len(entries) == 1 and entries[0].payment_status == "paired"
    assert (
        partition.invoices,
        partition.receipts,
        partition.trip_applications,
    ) == before
