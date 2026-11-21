import decimal
from datetime import date
from decimal import Decimal, Inexact, Rounded, localcontext

import pytest

from app.extraction.models import ExtractedDocument
from app.rules.documents import RuleDocument, partition_documents
from app.rules.pairing import PairedEntry, pair_documents
from app.rules.seed import load_cmi_default
from app.rules.transactions import build_transactions

_DAY = date(2026, 10, 9)


def _doc(doc_id, role="invoice", **fields):
    aliases = dict(
        zip(
            "amount day tip party airport".split(),
            "original_amount occurred_on tip_amount party_size airport_iata".split(),
            strict=True,
        )
    )
    values = {
        "doc_role": role,
        "merchant": "Restaurant",
        "original_amount": Decimal("25.00"),
        "currency": "USD",
        "occurred_on": _DAY,
        "confidence": 0.8,
        "source": "client_ocr",
    }
    values.update({aliases.get(key, key): value for key, value in fields.items()})
    return RuleDocument(doc_id, ExtractedDocument.model_validate(values))


def _entry(invoice=None, receipt=None, status="paired"):
    return PairedEntry(invoice, receipt, status)


def _build(*entries: PairedEntry, template=None):
    return build_transactions(entries, template or load_cmi_default()).drafts


def _with_rules(**updates):
    template = load_cmi_default()
    rules = template.compliance_rules.model_copy(update=updates)
    return template.model_copy(update={"compliance_rules": rules})


@pytest.mark.parametrize(
    ("merchant", "party", "airport", "transit", "expected", "category_present"),
    [
        ("Uber Restaurant", None, None, None, "Others", True),
        ("MTR", None, None, "octopus_mtr", "Others", True),
        ("Restaurant CAFÉ", None, None, None, "DRINK", True),
        ("奶茶店", None, None, None, "DRINK", True),
        ("Restaurant", None, None, None, "EAT", True),
        ("Unknown Shop", 2, None, None, "EAT", True),
        ("Unknown Shop", None, None, None, "UNKNOWN", True),
        ("Uber Coffee", None, "LAX", None, "TRANS", True),
        ("Restaurant", None, None, None, "UNKNOWN", False),
    ],
)
def test_category_priority_and_keywords(
    merchant, party, airport, transit, expected, category_present
):
    document = _doc("doc", merchant=merchant, party=party, airport=airport)
    entry = _entry(document)
    if transit:
        entry = PairedEntry(None, document, "exempted_transit", transit)
    template = load_cmi_default()
    if not category_present:
        template = template.model_copy(
            update={"categories": [c for c in template.categories if c.code != "EAT"]}
        )
    assert _build(entry, template=template)[0].category == expected


@pytest.mark.parametrize(
    ("merchant", "tip", "enabled", "expected_tip", "claim", "flag"),
    [
        ("Uber", Decimal("3.00"), True, Decimal("3.00"), Decimal("17.00"), True),
        ("CABÉ", Decimal("3.00"), True, Decimal("3.00"), Decimal("17.00"), True),
        ("Uber", Decimal("3.00"), False, Decimal("0.00"), Decimal("20.00"), False),
        ("Uber", Decimal("21.00"), True, Decimal("0.00"), Decimal("20.00"), False),
        ("Uber", Decimal("0.00"), True, Decimal("0.00"), Decimal("20.00"), False),
        ("Restaurant", Decimal("3.00"), True, Decimal("0.00"), Decimal("20.00"), False),
    ],
)
def test_ride_hailing_tip_exclusion(merchant, tip, enabled, expected_tip, claim, flag):
    default = load_cmi_default()
    tip_rules = default.compliance_rules.exclude_ride_hailing_tips.model_copy(
        update={"enabled": enabled, "providers": ["Uber", "Cabé"]}
    )
    template = _with_rules(exclude_ride_hailing_tips=tip_rules)
    draft = _build(
        _entry(_doc("doc", merchant=merchant, amount=Decimal("20"), tip=tip)),
        template=template,
    )[0]
    assert (draft.tip_amount, draft.claim_amount) == (expected_tip, claim)
    assert ("TIP_EXCLUDED" in draft.flags) is flag


@pytest.mark.parametrize(
    ("amount", "party", "merchant", "enabled", "claim", "count"),
    [
        (Decimal("100.00"), 3, "Restaurant", True, Decimal("33.33"), 3),
        (Decimal("10.00"), 3, "Restaurant", True, Decimal("3.33"), 3),
        (Decimal("0.05"), 2, "Restaurant", True, Decimal("0.03"), 2),
        (Decimal("10.00"), 3, "Coffee", True, Decimal("3.33"), 3),
        (Decimal("10.00"), 1, "Restaurant", True, Decimal("10.00"), 1),
        (Decimal("10.00"), 100, "Restaurant", True, Decimal("10.00"), 1),
        (Decimal("10.00"), 3, "Restaurant", False, Decimal("10.00"), 1),
    ],
)
def test_meal_split_rounds_half_up_and_obeys_rules(
    amount, party, merchant, enabled, claim, count
):
    template = _with_rules(meal_split_for_multiple_diners=enabled)
    draft = _build(
        _entry(_doc("doc", merchant=merchant, amount=amount, party=party)),
        template=template,
    )[0]
    assert (draft.claim_amount, draft.split_count) == (claim, count)
    assert ("MEAL_SPLIT" in draft.flags) is (count > 1)


def test_amount_math_ignores_default_and_callers_decimal_context(monkeypatch):
    template = load_cmi_default()
    ride = _entry(_doc("x", merchant="Uber", amount="123.45", tip=".01"))
    cases = (
        (_entry(_doc("x", amount="10.01", party=3)), "3.34"),
        (_entry(_doc("x", amount="10.01", party=2)), "5.01"),
        (_entry(_doc("x", amount="9999999999.93", party=2)), "4999999999.97"),
        (ride, "123.44"),
    )
    monkeypatch.setattr(decimal.DefaultContext, "prec", 3)
    monkeypatch.setattr(decimal.DefaultContext, "rounding", decimal.ROUND_DOWN)
    monkeypatch.setattr(decimal.DefaultContext, "Emax", 8)
    monkeypatch.setitem(decimal.DefaultContext.traps, Inexact, True)
    monkeypatch.setitem(decimal.DefaultContext.traps, Rounded, True)

    with localcontext() as context:
        context.prec = 3
        context.rounding = decimal.ROUND_DOWN
        context.Emax = 8
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        before = context.copy()
        for entry, expected in cases:
            draft = build_transactions((entry,), template).drafts[0]
            assert draft.claim_amount == Decimal(expected)
        assert (context.prec, context.rounding) == (before.prec, before.rounding)
        assert (context.flags, context.traps) == (before.flags, before.traps)


def test_currency_region_mapping_and_unmapped_flag() -> None:
    for currency, scope, detail in (
        ("USD", "in_scope", "美洲"),
        ("CNY", "out_of_scope", "大陆"),
        ("HKD", "out_of_scope", "HK"),
        ("SGD", "out_of_scope", "新加坡"),
        ("EUR", "out_of_scope", "其他"),
    ):
        draft = _build(_entry(_doc("doc", currency=currency)))[0]
        assert (draft.region_scope, draft.region_detail) == (scope, detail)
    template = load_cmi_default()
    other = template.region_hierarchy.out_of_scope.model_copy(
        update={"regions": template.region_hierarchy.out_of_scope.regions[:-1]}
    )
    hierarchy = template.region_hierarchy.model_copy(update={"out_of_scope": other})
    template = template.model_copy(update={"region_hierarchy": hierarchy})
    draft = _build(
        _entry(
            _doc("unmapped", merchant="Unknown", currency="EUR"),
            status="missing_payment",
        ),
        template=template,
    )[0]
    assert draft.flags == ("MISSING_PAYMENT", "UNKNOWN_CATEGORY", "REGION_UNMAPPED")


def test_invoice_fields_fall_back_to_receipt_and_preserve_document_ids() -> None:
    invoice = _doc("invoice", amount=None, currency=None, day=None, merchant=None)
    receipt = _doc(
        "receipt",
        "payment_receipt",
        merchant="Uber",
        amount=Decimal("12.50"),
        currency="HKD",
        tip=Decimal("1.00"),
        party=2,
        airport="HKG",
    )
    draft = _build(_entry(invoice, receipt))[0]
    assert (draft.merchant, draft.original_amount, draft.currency, draft.tx_date) == (
        "Uber",
        Decimal("12.50"),
        "HKD",
        _DAY,
    )
    assert (draft.airport_iata, draft.invoice_doc_id, draft.receipt_doc_id) == (
        "HKG",
        "invoice",
        "receipt",
    )
    assert draft.claim_amount == Decimal("11.50")
    assert draft.tip_amount == Decimal("1.00")
    for missing in ({"day": None}, {"amount": None}, {"currency": None}):
        invoice = _doc("invoice", **missing)
        receipt = _doc("receipt", "payment_receipt", **missing)
        result = build_transactions((_entry(invoice, receipt),), load_cmi_default())
        assert result.drafts == ()
        assert result.incomplete_doc_ids == ("invoice", "receipt")


def test_pairing_to_transaction_integration() -> None:
    documents = (
        _doc("missing", merchant="Uber", amount=Decimal("20"), tip=Decimal("2")),
        _doc("invoice", merchant="Coffee House", amount=Decimal("15")),
        _doc(
            "receipt", "payment_receipt", merchant="Coffee House", amount=Decimal("15")
        ),
    )
    rules = load_cmi_default().compliance_rules
    partition = partition_documents(documents, rules)
    entries = pair_documents(partition, rules)
    drafts = build_transactions(entries, load_cmi_default()).drafts
    assert [(draft.invoice_doc_id, draft.payment_status) for draft in drafts] == [
        ("missing", "missing_payment"),
        ("invoice", "paired"),
    ]
    assert drafts[0].flags == ("TIP_EXCLUDED", "MISSING_PAYMENT")
    assert drafts[1].receipt_doc_id == "receipt"
