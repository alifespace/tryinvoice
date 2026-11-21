from datetime import UTC, date, time, timedelta, timezone
from decimal import Decimal

import pytest

from app.extraction.models import ExtractedDocument
from app.rules.documents import RuleDocument, partition_documents
from app.rules.seed import load_cmi_default


def _rules(**updates: bool):
    return load_cmi_default().compliance_rules.model_copy(update=updates)


def _document(
    doc_id: str,
    role: str = "payment_receipt",
    *,
    merchant: str | None = "Starbucks Coffee",
    amount: Decimal | None = Decimal("25.00"),
    currency: str | None = "USD",
    occurred_on: date | None = date(2026, 10, 9),
    occurred_time: time | None = time(12, 0),
    reference_no: str | None = None,
    card_last4: str | None = None,
    confidence: float = 0.8,
    quality_score: float | None = None,
    overrides: dict[str, object] | None = None,
) -> RuleDocument:
    fields: dict[str, object] = dict(
        doc_role=role,
        merchant=merchant,
        original_amount=amount,
        currency=currency,
        occurred_on=occurred_on,
        occurred_time=occurred_time,
        reference_no=reference_no,
        card_last4=card_last4,
        confidence=confidence,
        source="client_ocr",
    )
    fields.update(overrides or {})
    return RuleDocument(doc_id, ExtractedDocument.model_validate(fields), quality_score)


def _partition(*documents: RuleDocument, **updates: bool):
    return partition_documents(documents, _rules(**updates))


def test_partition_routes_all_roles_and_applies_irrelevant_switch() -> None:
    documents = (
        _document("inv", "invoice"),
        _document("receipt", "payment_receipt"),
        _document("trip", "trip_application"),
        _document("irrelevant", "irrelevant"),
        _document("unknown", "unknown"),
    )
    filtered = partition_documents(documents, _rules())
    assert tuple(item.doc_id for item in filtered.invoices) == ("inv",)
    assert tuple(item.doc_id for item in filtered.receipts) == ("receipt",)
    assert tuple(item.doc_id for item in filtered.trip_applications) == ("trip",)
    assert filtered.irrelevant_ids == ("irrelevant",)
    assert tuple(item.doc_id for item in filtered.unknown) == ("unknown",)

    retained = partition_documents(documents, _rules(filter_irrelevant_files=False))
    assert tuple(item.doc_id for item in retained.unknown) == ("irrelevant", "unknown")


def test_reshot_cluster_keeps_best_quality_then_confidence_then_upload_order() -> None:
    documents = (
        _document("first", quality_score=0.7, confidence=0.9),
        _document("best", quality_score=0.9, confidence=0.7),
        _document("third", quality_score=0.7, confidence=0.8),
        _document("receipt", "invoice", quality_score=0.7, confidence=0.8),
    )
    result = partition_documents(documents, _rules())
    assert tuple(item.doc_id for item in result.receipts) == ("best",)
    assert result.duplicate_of == {"first": "best", "third": "best"}
    assert tuple(item.doc_id for item in result.invoices) == ("receipt",)

    tied = _partition(_document("earlier"), _document("later"))
    assert tuple(item.doc_id for item in tied.receipts) == ("earlier",)
    assert tied.duplicate_of == {"later": "earlier"}

    confidence_tie = _partition(
        _document("lower-confidence", quality_score=0.6, confidence=0.7),
        _document("higher-confidence", quality_score=0.6, confidence=0.9),
    )
    assert confidence_tie.receipts[0].doc_id == "higher-confidence"


@pytest.mark.parametrize(
    ("first_changes", "second_changes"),
    [
        ({}, {"original_amount": Decimal("26.00")}),
        ({}, {"currency": "HKD"}),
        ({}, {"occurred_on": date(2026, 10, 10)}),
        ({}, {"original_amount": None}),
        ({}, {"currency": None}),
        ({}, {"occurred_on": None}),
        ({}, {"occurred_time": time(12, 1, 1)}),
        ({"reference_no": "ref-1"}, {"reference_no": "other-ref"}),
        ({"card_last4": "1234"}, {"card_last4": "5678"}),
        ({}, {"merchant": "Unrelated Shop"}),
        ({}, {"merchant": None}),
    ],
)
def test_non_reshot_pairs_remain_separate(
    first_changes: dict[str, object], second_changes: dict[str, object]
) -> None:
    result = _partition(
        _document("original", overrides=first_changes),
        _document("candidate", overrides=second_changes),
    )
    assert tuple(item.doc_id for item in result.receipts) == ("original", "candidate")
    assert result.duplicate_of == {}


def test_missing_time_and_matching_reference_allow_reshot() -> None:
    for first, second in (
        (
            _document("first", merchant="Cafe A", reference_no=" Ref 42 "),
            _document("second", merchant="Cafe B", reference_no="ref42"),
        ),
        (_document("first"), _document("second", merchant="STARBUCKS  COFFEE.")),
    ):
        assert len(_partition(first, second).receipts) == 1

    day = date(2026, 10, 9)
    early = timezone(timedelta(hours=23, minutes=59))
    late = timezone(-timedelta(hours=23, minutes=59))
    utc, utc_plus_one = map(time.fromisoformat, ("12:00:00+00:00", "12:00:00+01:00"))
    cases = (
        (day, time(23, 59, 30), None, 1),
        (day, time(12, 0), time(12, 1, tzinfo=UTC), 1),
        (day, time(12, 0), time(12, 1, 1, tzinfo=UTC), 2),
        (day, utc, utc_plus_one, 2),
        (day, utc, time.fromisoformat("13:00:00+01:00"), 1),
        (date.min, time(0, 0, tzinfo=early), time(0, 0, 59, tzinfo=early), 1),
        (date.max, time(23, 59, tzinfo=late), time(23, 59, 59, tzinfo=late), 1),
    )
    for occurred_on, first_time, second_time, count in cases:
        for first, second in ((first_time, second_time), (second_time, first_time)):
            result = _partition(
                _document("first", occurred_on=occurred_on, occurred_time=first),
                _document("second", occurred_on=occurred_on, occurred_time=second),
            )
            assert len(result.receipts) == count


def test_deduplication_is_role_local_and_can_be_disabled() -> None:
    documents = (
        _document("receipt", "payment_receipt"),
        _document("invoice", "invoice"),
        _document("invoice-reshot", "invoice"),
    )
    result = partition_documents(documents, _rules())
    assert tuple(item.doc_id for item in result.receipts) == ("receipt",)
    assert tuple(item.doc_id for item in result.invoices) == ("invoice",)
    assert result.duplicate_of == {"invoice-reshot": "invoice"}

    disabled = partition_documents(
        (_document("first"), _document("second")),
        _rules(deduplicate_reshot_receipts=False),
    )
    assert tuple(item.doc_id for item in disabled.receipts) == ("first", "second")
    assert disabled.duplicate_of == {}


def test_input_is_unchanged_and_quality_winner_is_order_independent() -> None:
    documents = (
        _document("low", merchant="AAAABC", quality_score=0.2),
        _document("high", merchant="AAAACBAC", quality_score=0.9),
    )
    before = tuple(item.extracted.model_dump() for item in documents)
    first = partition_documents(documents, _rules())
    shuffled = partition_documents(tuple(reversed(documents)), _rules())
    assert tuple(item.extracted.model_dump() for item in documents) == before
    assert len(first.receipts) == len(shuffled.receipts) == 1
    assert first.receipts[0].doc_id == shuffled.receipts[0].doc_id == "high"
