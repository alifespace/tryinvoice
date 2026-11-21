import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from typing import Literal, cast

from app.rules.dsl import RegionScope, RuleTemplateDSL
from app.rules.pairing import PairedEntry, PaymentStatus

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")
_MONEY_CONTEXT = Context(
    prec=28,
    rounding=ROUND_HALF_UP,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[],
)
_DRINK = re.compile(
    r"(?<![A-Za-z0-9_])(?:coffee|cafe|café|tea|boba|starbucks)(?![A-Za-z0-9_])|咖啡|奶茶|茶"
)
_FOOD = re.compile(
    r"(?<![A-Za-z0-9_])(?:restaurant|grill|kitchen|diner|pizza|burger|bistro|bar)"
    r"(?![A-Za-z0-9_])|餐|饭|面|火锅|烧烤"
)


@dataclass(frozen=True, slots=True)
class TransactionDraft:
    category: str
    region_scope: Literal["in_scope", "out_of_scope"]
    region_detail: str
    tx_date: date
    merchant: str
    currency: str
    original_amount: Decimal
    tip_amount: Decimal
    split_count: int
    claim_amount: Decimal
    airport_direction: Literal["To", "From"] | None
    airport_iata: str | None
    payment_status: PaymentStatus
    flags: tuple[str, ...]
    invoice_doc_id: str | None
    receipt_doc_id: str | None


@dataclass(frozen=True, slots=True)
class TransactionBuildResult:
    drafts: tuple[TransactionDraft, ...]
    incomplete_doc_ids: tuple[str, ...]


def _pick(
    entry: PairedEntry,
    field: str,
    *,
    zero_missing: bool = False,
    skip_blank: bool = False,
) -> object:
    for doc in (entry.invoice, entry.receipt):
        if doc is not None:
            value = getattr(doc.extracted, field)
            if value is not None and not (zero_missing and value == _ZERO):
                if not skip_blank or not isinstance(value, str) or value.strip():
                    return value
    return _ZERO if zero_missing else None


def _category(
    entry: PairedEntry,
    merchant: str,
    party_size: int | None,
    ride_hailing: bool,
    template: RuleTemplateDSL,
) -> str:
    checks = (
        (_pick(entry, "airport_iata", skip_blank=True), "TRANS"),
        (entry.transit_kind is not None or ride_hailing, "Others"),
        (_DRINK.search(merchant.casefold()), "DRINK"),
        (_FOOD.search(merchant.casefold()) or party_size is not None, "EAT"),
    )
    candidate = next((code for matched, code in checks if matched), "UNKNOWN")
    available = {category.code for category in template.categories}
    return candidate if candidate in available else "UNKNOWN"


def _region(
    currency: str, template: RuleTemplateDSL
) -> tuple[Literal["in_scope", "out_of_scope"], str, bool]:
    hierarchy = template.region_hierarchy
    scopes: tuple[tuple[Literal["in_scope", "out_of_scope"], RegionScope], ...] = (
        ("in_scope", hierarchy.in_scope),
        ("out_of_scope", hierarchy.out_of_scope),
    )
    for code in (currency, "*"):
        for name, scope in scopes:
            region = next((r for r in scope.regions if code in r.currencies), None)
            if region:
                return name, region.label, False
    return "out_of_scope", "其他", True


def _amounts(
    amount: Decimal,
    tip: Decimal,
    party_size: int | None,
    category: str,
    ride_hailing: bool,
    template: RuleTemplateDSL,
) -> tuple[Decimal, int, Decimal, list[str]]:
    rules = template.compliance_rules
    tips = rules.exclude_ride_hailing_tips
    excluded_tip = (
        tip if tips.enabled and ride_hailing and _ZERO < tip <= amount else _ZERO
    )
    count = party_size or 1
    should_split = all(
        (
            rules.meal_split_for_multiple_diners,
            category in {"EAT", "DRINK"},
            party_size is not None,
            2 <= count <= 99,
        )
    )
    split = count if should_split else 1
    with localcontext(_MONEY_CONTEXT):
        claim = (
            (amount / split).quantize(_CENT, rounding=ROUND_HALF_UP)
            if split > 1
            else amount - excluded_tip
        )
    flags = [tips.highlight_flag] if excluded_tip else []
    if split > 1:
        flags.append("MEAL_SPLIT")
    return excluded_tip, split, claim, flags


def _draft(
    entry: PairedEntry,
    core: tuple[date, str, Decimal],
    template: RuleTemplateDSL,
    providers: re.Pattern[str] | None,
) -> TransactionDraft:
    day, currency, amount = core
    merchant = cast(str | None, _pick(entry, "merchant")) or ""
    party_size = cast(int | None, _pick(entry, "party_size"))
    raw_tip = cast(Decimal, _pick(entry, "tip_amount", zero_missing=True))
    ride_hailing = bool(providers and providers.search(merchant.casefold()))
    category = _category(entry, merchant, party_size, ride_hailing, template)
    tip, split, claim, amount_flags = _amounts(
        amount, raw_tip, party_size, category, ride_hailing, template
    )
    scope, detail, unmapped = _region(currency, template)
    flag_tuple = tuple(
        dict.fromkeys(
            amount_flags
            + (["MISSING_PAYMENT"] if entry.payment_status == "missing_payment" else [])
            + (["MISSING_INVOICE"] if entry.payment_status == "missing_invoice" else [])
            + (["UNKNOWN_CATEGORY"] if category == "UNKNOWN" else [])
            + (["REGION_UNMAPPED"] if unmapped else [])
        )
    )
    airport = cast(str | None, _pick(entry, "airport_iata", skip_blank=True))
    airport = airport.strip() if airport else None
    return TransactionDraft(
        category,
        scope,
        detail,
        day,
        merchant,
        currency,
        amount,
        tip,
        split,
        claim,
        None,
        airport,
        entry.payment_status,
        flag_tuple,
        entry.invoice.doc_id if entry.invoice else None,
        entry.receipt.doc_id if entry.receipt else None,
    )


def build_transactions(
    entries: Sequence[PairedEntry], template: RuleTemplateDSL
) -> TransactionBuildResult:
    provider_names = template.compliance_rules.exclude_ride_hailing_tips.providers
    names = "|".join(
        re.escape(p.strip().casefold()) for p in provider_names if p.strip()
    )
    pattern = r"(?<![A-Za-z0-9_])(?:" + names + r")(?![A-Za-z0-9_])"
    providers = re.compile(pattern) if names else None
    drafts: list[TransactionDraft] = []
    incomplete: list[str] = []
    for entry in entries:
        core = tuple(
            _pick(entry, field)
            for field in ("occurred_on", "currency", "original_amount")
        )
        if None in core:
            incomplete.extend(d.doc_id for d in (entry.invoice, entry.receipt) if d)
        else:
            drafts.append(
                _draft(
                    entry, cast(tuple[date, str, Decimal], core), template, providers
                )
            )
    return TransactionBuildResult(tuple(drafts), tuple(dict.fromkeys(incomplete)))
