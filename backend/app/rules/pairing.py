import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from app.rules.documents import PartitionResult, RuleDocument
from app.rules.dsl import ComplianceRules

PaymentStatus = Literal[
    "paired", "missing_payment", "missing_invoice", "exempted_transit"
]
TransitKind = Literal["octopus_mtr", "credit_card_public_transit"]

_MTR = re.compile(r"\bMTR\b|港铁|港鐵|地铁|地鐵", re.IGNORECASE | re.ASCII)
_PUBLIC_TRANSIT = re.compile(
    r"\b(?:MTA|NYCT|SFMTA|MUNI|BART|TfL|Transport\s+for\s+London|SMRT|SBS\s+Transit)\b"
    r"|地铁|地鐵|公交|轻轨|輕軌",
    re.IGNORECASE | re.ASCII,
)


@dataclass(frozen=True, slots=True)
class PairedEntry:
    invoice: RuleDocument | None
    receipt: RuleDocument | None
    payment_status: PaymentStatus
    transit_kind: TransitKind | None = None

    def __post_init__(self) -> None:
        if self.invoice is None and self.receipt is None:
            raise ValueError("配对结果至少需要一张凭证。")
        if self.transit_kind is not None and self.payment_status != "exempted_transit":
            raise ValueError("交通类型仅适用于交通免除状态。")

    @property
    def primary(self) -> RuleDocument:
        return self.invoice or self.receipt  # type: ignore[return-value]


def _merchant(value: str | None) -> str:
    return "".join(char for char in (value or "").casefold() if char.isalnum())


def _merchant_score(first: str | None, second: str | None) -> float:
    left, right = _merchant(first), _merchant(second)
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 1.0
    return SequenceMatcher(None, min(left, right), max(left, right)).ratio()


def _merchants_compatible(first: str | None, second: str | None) -> bool:
    left, right = _merchant(first), _merchant(second)
    return not left or not right or _merchant_score(first, second) >= 0.6


def _core_fields_match(invoice: RuleDocument, receipt: RuleDocument) -> bool:
    left, right = invoice.extracted, receipt.extracted
    return (
        left.currency is not None
        and left.currency == right.currency
        and left.original_amount is not None
        and left.original_amount == right.original_amount
    )


def _candidate_key(
    invoice: RuleDocument,
    receipt: RuleDocument,
    invoice_index: int,
    receipt_index: int,
    rules: ComplianceRules,
) -> tuple[int, float, int, int, int] | None:
    if not _core_fields_match(invoice, receipt):
        return None
    left, right = invoice.extracted, receipt.extracted
    if left.occurred_on is None or right.occurred_on is None:
        return None
    date_delta = abs((left.occurred_on - right.occurred_on).days)
    if date_delta > rules.timezone_tolerance_days or (
        left.card_last4 and right.card_last4 and left.card_last4 != right.card_last4
    ):
        return None
    if not _merchants_compatible(left.merchant, right.merchant):
        return None
    card_match = int(left.card_last4 is None or right.card_last4 is None)
    return (
        date_delta,
        -_merchant_score(left.merchant, right.merchant),
        card_match,
        invoice_index,
        receipt_index,
    )


def _octopus_mtr(receipt: RuleDocument, rules: ComplianceRules) -> bool:
    document = receipt.extracted
    merchant = document.merchant or ""
    return (
        "octopus_mtr" in rules.transit_direct_pay_exemptions
        and document.currency == "HKD"
        and _MTR.search(merchant) is not None
    )


def _credit_card_transit(receipt: RuleDocument, rules: ComplianceRules) -> bool:
    document = receipt.extracted
    merchant = document.merchant or ""
    return (
        "credit_card_public_transit" in rules.transit_direct_pay_exemptions
        and document.card_last4 is not None
        and _PUBLIC_TRANSIT.search(merchant) is not None
    )


def _transit_kind(receipt: RuleDocument, r: ComplianceRules) -> TransitKind | None:
    if _octopus_mtr(receipt, r):
        return "octopus_mtr"
    return "credit_card_public_transit" if _credit_card_transit(receipt, r) else None


def _unmatched_receipt(receipt: RuleDocument, rules: ComplianceRules) -> PairedEntry:
    if not rules.require_invoice_payment_closure:
        return PairedEntry(None, receipt, "paired")
    kind = _transit_kind(receipt, rules)
    return PairedEntry(
        None, receipt, "exempted_transit" if kind else "missing_invoice", kind
    )


def _assign_candidates(
    candidates: list[tuple[tuple[int, float, int, int, int], int, int]],
) -> tuple[dict[int, int], set[int]]:
    invoice_matches: dict[int, int] = {}
    used_receipts: set[int] = set()
    for _, invoice_index, receipt_index in candidates:
        if invoice_index not in invoice_matches and receipt_index not in used_receipts:
            invoice_matches[invoice_index] = receipt_index
            used_receipts.add(receipt_index)
    return invoice_matches, used_receipts


def _invoice_entries(
    invoices: tuple[RuleDocument, ...],
    receipts: tuple[RuleDocument, ...],
    matches: dict[int, int],
    closure_required: bool,
) -> tuple[PairedEntry, ...]:
    return tuple(
        PairedEntry(
            invoice,
            receipts[matches[i]] if i in matches else None,
            "paired" if i in matches or not closure_required else "missing_payment",
        )
        for i, invoice in enumerate(invoices)
    )


def _receipt_entries(
    receipts: tuple[RuleDocument, ...],
    used_receipts: set[int],
    rules: ComplianceRules,
) -> tuple[PairedEntry, ...]:
    return tuple(
        _unmatched_receipt(receipt, rules)
        for index, receipt in enumerate(receipts)
        if index not in used_receipts
    )


def pair_documents(
    partition: PartitionResult, rules: ComplianceRules
) -> tuple[PairedEntry, ...]:
    candidates = sorted(
        (key, i, j)
        for i, invoice in enumerate(partition.invoices)
        for j, receipt in enumerate(partition.receipts)
        if (key := _candidate_key(invoice, receipt, i, j, rules)) is not None
    )
    invoice_matches, used_receipts = _assign_candidates(candidates)
    entries = _invoice_entries(
        partition.invoices,
        partition.receipts,
        invoice_matches,
        rules.require_invoice_payment_closure,
    )
    return entries + _receipt_entries(partition.receipts, used_receipts, rules)
