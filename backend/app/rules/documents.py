from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from itertools import combinations
from types import MappingProxyType

from app.extraction.models import ExtractedDocument
from app.rules.dsl import ComplianceRules


@dataclass(frozen=True, slots=True)
class RuleDocument:
    doc_id: str
    extracted: ExtractedDocument
    quality_score: float | None = None


@dataclass(frozen=True, slots=True)
class PartitionResult:
    invoices: tuple[RuleDocument, ...]
    receipts: tuple[RuleDocument, ...]
    trip_applications: tuple[RuleDocument, ...]
    irrelevant_ids: tuple[str, ...]
    unknown: tuple[RuleDocument, ...]
    duplicate_of: Mapping[str, str]


def _normalized_merchant(value: str | None) -> str:
    return "".join(c for c in (value or "").casefold() if c.isalnum())


def _normalized_reference(value: str | None) -> str | None:
    return "".join(value.split()).casefold() or None if value is not None else None


def _same_core_fields(first: ExtractedDocument, second: ExtractedDocument) -> bool:
    values = (first.currency, first.original_amount, first.occurred_on)
    return None not in values and values == (
        second.currency,
        second.original_amount,
        second.occurred_on,
    )


def _times_match(first: RuleDocument, second: RuleDocument) -> bool:
    first_time = first.extracted.occurred_time
    second_time = second.extracted.occurred_time
    if first_time is None or second_time is None:
        return True

    comparison_date = first.extracted.occurred_on or date(2000, 1, 2)
    left, right = (
        datetime.combine(comparison_date, value) for value in (first_time, second_time)
    )
    left_offset, right_offset = left.utcoffset(), right.utcoffset()
    local_delta = left.replace(tzinfo=None) - right.replace(tzinfo=None)
    if left_offset is not None and right_offset is not None:
        local_delta -= left_offset - right_offset
    return abs(local_delta) <= timedelta(seconds=60)


def _merchants_match(first: str | None, second: str | None) -> bool:
    left, right = sorted((_normalized_merchant(first), _normalized_merchant(second)))
    return bool(left and right and SequenceMatcher(None, left, right).ratio() >= 0.85)


def _identifiers_match(first: ExtractedDocument, second: ExtractedDocument) -> bool:
    left = _normalized_reference(first.reference_no)
    right = _normalized_reference(second.reference_no)
    if left is not None and right is not None:
        return left == right
    return _merchants_match(first.merchant, second.merchant)


def _cards_match(first: str | None, second: str | None) -> bool:
    return first is None or second is None or first == second


def _is_reshot(first: RuleDocument, second: RuleDocument) -> bool:
    left, right = first.extracted, second.extracted
    return all(
        (
            _same_core_fields(left, right),
            _times_match(first, second),
            _identifiers_match(left, right),
            _cards_match(left.card_last4, right.card_last4),
        )
    )


def _find(parent: list[int], index: int) -> int:
    while parent[index] != index:
        parent[index] = parent[parent[index]]
        index = parent[index]
    return index


def _union(parent: list[int], first: int, second: int) -> None:
    first_root = _find(parent, first)
    second_root = _find(parent, second)
    if first_root != second_root:
        parent[second_root] = first_root


def _deduplicate(
    documents: Sequence[RuleDocument],
) -> tuple[tuple[RuleDocument, ...], dict[str, str]]:
    parent = list(range(len(documents)))
    for first, second in combinations(range(len(documents)), 2):
        if _is_reshot(documents[first], documents[second]):
            _union(parent, first, second)

    clusters: dict[int, list[int]] = {}
    for index in range(len(documents)):
        clusters.setdefault(_find(parent, index), []).append(index)

    kept: set[int] = set()
    duplicate_of: dict[str, str] = {}
    for indices in clusters.values():
        representative = max(
            indices,
            key=lambda i: (
                documents[i].quality_score or 0.0,
                documents[i].extracted.confidence,
                -i,
            ),
        )
        kept.add(representative)
        for index in indices:
            if index != representative:
                duplicate_of[documents[index].doc_id] = documents[representative].doc_id
    return tuple(doc for i, doc in enumerate(documents) if i in kept), duplicate_of


def partition_documents(
    docs: Sequence[RuleDocument], rules: ComplianceRules
) -> PartitionResult:
    groups: dict[str, list[RuleDocument]] = {
        "invoice": [],
        "payment_receipt": [],
        "trip_application": [],
    }
    irrelevant_ids: list[str] = []
    unknown: list[RuleDocument] = []

    for document in docs:
        role = document.extracted.doc_role
        if role == "irrelevant" and rules.filter_irrelevant_files:
            irrelevant_ids.append(document.doc_id)
        elif role in groups:
            groups[role].append(document)
        else:
            unknown.append(document)

    duplicate_of: dict[str, str] = {}
    if rules.deduplicate_reshot_receipts:
        for role in ("invoice", "payment_receipt"):
            kept, duplicates = _deduplicate(groups[role])
            groups[role] = list(kept)
            duplicate_of.update(duplicates)

    return PartitionResult(
        invoices=tuple(groups["invoice"]),
        receipts=tuple(groups["payment_receipt"]),
        trip_applications=tuple(groups["trip_application"]),
        irrelevant_ids=tuple(irrelevant_ids),
        unknown=tuple(unknown),
        duplicate_of=MappingProxyType(duplicate_of),
    )
