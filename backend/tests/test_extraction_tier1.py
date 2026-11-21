from datetime import date
from decimal import Decimal
from itertools import cycle, product

import pymupdf
import pytest
from pydantic import ValidationError

from app.extraction.models import ExtractedDocument, ExtractionInput
from app.extraction.parser import parse_text
from app.extraction.tier1 import DeterministicExtractor


@pytest.mark.parametrize(
    ("text", "role", "amount", "currency"),
    [
        (
            "Uber\nReceipt\nDate: 2026-10-09\nTotal: US$ 24.50\nPaid",
            "payment_receipt",
            Decimal("24.50"),
            "USD",
        ),
        (
            "Grab\nInvoice\nInvoice No: G-123\nDate: 09 Oct 2026\nTotal: SGD 21.23",
            "invoice",
            Decimal("21.23"),
            "SGD",
        ),
        (
            "Cafe\nReceipt\nDate: 2026-10-09\nTotal: HK$ 42.00\nTip: 5.00\n"
            "Guests: 2\nCard: 4111111111111111",
            "payment_receipt",
            Decimal("42.00"),
            "HKD",
        ),
        (
            "Business Trip Application\nStart: 2026-10-09\nEnd: 2026-10-11\n"
            "Destination Airport: LAX\nAirport: HKG",
            "trip_application",
            None,
            None,
        ),
        ("Chat screenshot\nSee you at 5", "irrelevant", None, None),
        (" \n ", "unknown", None, None),
    ],
    ids=(
        "uber-receipt",
        "grab-invoice",
        "restaurant-card",
        "trip-application",
        "irrelevant",
        "empty",
    ),
)
def test_parse_text_core_documents(
    text: str, role: str, amount: Decimal | None, currency: str | None
) -> None:
    document = parse_text(text, source="client_ocr")

    assert document.doc_role == role
    assert document.original_amount == amount
    assert document.currency == currency
    if "4111111111111111" in text:
        assert document.card_last4 == "1111"


def test_parse_trip_dates_and_airports() -> None:
    document = parse_text(
        "Travel Request\nStart: 2026-10-09\nEnd: 2026-10-11\n"
        "Destination Airport: LAX\nDestination Airport: HKG",
        source="pdf",
    )

    assert document.trip_start_date == date(2026, 10, 9)
    assert document.trip_end_date == date(2026, 10, 11)
    assert document.destination_airports == ["LAX", "HKG"]
    assert document.airport_iata == "LAX"


def test_parse_labels_currency_time_and_reference_fields() -> None:
    document = parse_text(
        "\nMerchant: Cafe\nInvoice\nDate: 09 Oct 2026\nTime: 5:30 PM\n"
        "Total: 10.00\nCurrency: EUR\nTax: 5.00\nVAT 20%\nTip: 2.50\nParty of 3\n"
        "Card ending in 4242\nReceipt No: R-42\nPaid",
        source="client_ocr",
    )

    assert document.merchant == "Cafe"
    assert document.occurred_time is not None
    assert document.occurred_time.hour == 17
    assert document.original_amount == Decimal("10.00")
    assert document.currency == "EUR"
    assert document.tip_amount == Decimal("2.50")
    assert document.party_size == 3
    assert document.card_last4 == "4242"
    assert document.reference_no == "R-42"
    assert document.has_paid_marker


def test_currency_symbol_and_masked_card_are_recognized() -> None:
    document = parse_text("Receipt\nCurrency: $\nCard: **** 4242", source="client_ocr")

    assert (document.currency, document.card_last4) == ("USD", "4242")


def test_standalone_masked_card_is_extracted_without_becoming_merchant() -> None:
    document = parse_text("Receipt\n**** 4242", source="client_ocr")

    assert document.card_last4 == "4242"
    assert document.merchant is None


# fmt: off
_FIELDS = ("", "Merchant: ", "Total: ", "Tip: ", "Guests: ", "Invoice No: ")
_STYLE_LABELS = "Tax Number|Taxpayer ID|VAT Reg|GST Number|HST Reg|PST ID|Passport|National ID".split("|")  # noqa: E501
_SPACE_VARIANTS = (" ", " ", "　", " ")
_SPACE_BY_LABEL = dict(zip(_STYLE_LABELS, cycle(_SPACE_VARIANTS), strict=False))
def _matrix_case(label: str, value: str, suffix: str = ": ", split: bool = False) -> tuple[str, str]:  # noqa: E501
    return "\n".join(f"{field}{label}{suffix}{'' if split else value}" + (f"\n{field}{value}" if split else "") for field in _FIELDS), value  # noqa: E501
_LABELS = [f"{w}{s}{suffix}" for w, s, suffix in product("Tax|VAT|GST|Taxpayer".split("|"), " .-_", "No|No.|Number|ID|#|Reg|Reg.No.|Regn. No.|Registration ID|Identification Number".split("|"))] + (  # noqa: E501
    "TIN|GSTIN|SSN|Passport No|ID Number|ID #|ID#|证件号|税号|纳税人识别号|统一社会信用代码|STRIP ID #|XTRIP ID#|税务登记号|Tax/Reg/No|HST Reg|商户Tax Number|Tax Number号码|商户VAT.Reg.No.号码|Tax Identification No|ABC税号|"  # noqa: E501
    "Tax Identification No:\nHST Reg:\nTotal: 123456789\nTax Identification No:\n\nHST Reg:\nTip: 123456789\nTax Identification No:\nHST Reg:\nInvoice No|Tax Number:\n|ＶＡＴ　Ｎｏ：１２３４５６７８９|Tax Number: VAT Reg:\nTip|Tax Number:\nTax Number: VAT Reg:\nTip|Passport Identification Number:\nTotal|National ID Registration No:\nTotal|Social Security ID Number:\nTotal|SSN Number:\nTotal"  # noqa: E501
).split("|")
_LABELS = [label for label in _LABELS if label not in _STYLE_LABELS]
_VALUES = "1234567890123456|12-3456789|123-45-6789|4111-1111-1111-4242|Card 4111111111111111|987654321".split("|")  # noqa: E501
_UNTAGGED = "商户123-45-6789号|费用12-3456789号|卡4111111111111111号|Card 4111111111111111".split("|")  # noqa: E501
_CERTIFICATE_LABELS = [f"{first}{separator}{second}" for first, second in (("Social", "Security"), ("National", "ID")) for separator in (" ", "_", "-", "/", ".", "")] + ["Passport", "ID", "SSN"]  # noqa: E501
_SHAPES = (("#", False), (": ", False), ("#", True), (": ", True))
_SENSITIVE_CASES = [
    *(_matrix_case(label, "123456789", "" if label.endswith("#") else ": ") for label in _LABELS),  # noqa: E501
    *(_matrix_case(label, value) for label, value in product(("Tax Number", "VAT.Reg.No.", "GST_ID"), _VALUES)),  # noqa: E501
    *(_matrix_case("", value, "") for value in _UNTAGGED),
    *(_matrix_case(label, "987654321", style) for label in _STYLE_LABELS for style in (": ", " #", "#", "：", f"{_SPACE_BY_LABEL[label]}: ")),  # noqa: E501
    *(("\n".join(f"Tax Number{space}:\n{prefix}987654321" for prefix in (_FIELDS[1], _FIELDS[2], _FIELDS[5])), "987654321") for space in _SPACE_VARIANTS),  # noqa: E501
    *(_matrix_case(label, "987654321", punctuation, split) for label in _CERTIFICATE_LABELS for punctuation, split in _SHAPES if label != "ID" or punctuation == "#"),  # noqa: E501
    *(_matrix_case(f"{label} Number", "987654321", ":", True) for label in _CERTIFICATE_LABELS),  # noqa: E501
    *(
        _matrix_case(f"{label}_{suffix}", "987654321", punctuation, split)
        for label in ("SSN", "TIN", "EIN", "GSTIN")
        for suffix in ("No", "Number", "ID")
        for punctuation, split in (("#", False), (": ", False), (":", True))
    ),
    *(
        (
            f"{first}{newline}{second}:{newline}{field}987654321",
            "987654321",
        )
        for first, second in (("Tax", "Identification Number"), ("SSN", "Number"))
        for newline in ("\n", "\r", "\r\n")
        for field in _FIELDS
    ),
    *((newline.join(("Business Trip", "ID #:", f"{field}987654321")), "987654321") for newline, field in product(("\n", "\r", "\r\n"), _FIELDS)),  # noqa: E501
    *((newline.join((label, marker, f"{field}987654321")), "987654321") for label, marker, newline, field in product(("税号", "证件号", "Tax Number", "Passport", "National ID", "SSN", "Tax ID"), ("#", "＃", ":", "："), ("\n", "\r", "\r\n"), _FIELDS)),  # noqa: E501
    *((newline.join(("Tax Number", ":", ":", f"{field}987654321")), "987654321") for newline, field in product(("\n", "\r", "\r\n"), _FIELDS)),  # noqa: E501
    *((newline.join(("Tax Number", ":", "", f"{field}987654321")), "987654321") for newline, field in product(("\n", "\r", "\r\n"), _FIELDS)),  # noqa: E501
]
# fmt: on


@pytest.mark.parametrize(
    "case_index", range(len(_SENSITIVE_CASES)), ids=lambda _: "safe"
)
def test_sensitive_text_matrix_is_redacted(case_index: int) -> None:
    text, value = _SENSITIVE_CASES[case_index]
    document = parse_text(f"{text}\nCafe\nInvoice\nTotal: $10.00", source="client_ocr")
    if value and value in document.model_dump_json():
        pytest.fail("sensitive value was not redacted")
    if document.original_amount != Decimal("10.00"):
        pytest.fail("normal field after sensitive value was not preserved")
    if document.merchant != "Cafe":
        pytest.fail("sensitive value was not redacted")


def test_normal_values_and_names_survive_sensitive_input_sanitization() -> None:
    document = parse_text(
        "Cafe\nInvoice\nTax: 5.00\nVAT 20%\nTax Included\nID: 5\nTotal: $1,234.56\n"
        "Tip: 2.00\n"
        "Guests: 3\nTrip ID #TRIP-2024-001\nCard: **** 4242",
        source="client_ocr",
    )
    assert document.original_amount == Decimal("1234.56")
    assert document.tip_amount == Decimal("2.00")
    assert document.party_size == 3
    assert document.reference_no == "TRIP-2024-001"
    assert all(
        parse_text(f"Trip{gap}ID{marker}TRIP-42", source="client_ocr").reference_no
        == "TRIP-42"
        for gap, marker in product(("  ", "\t\t", "\u2003\u2003"), (" #", "#"))
    )
    assert [
        parse_text(f"{name}\nTotal: $1.00", source="client_ocr").merchant
        for name in ("Tina's Kitchen", "Einstein Bros", "TIN_Store")
    ] == ["Tina's Kitchen", "Einstein Bros", "TIN_Store"]


def test_punctuation_without_pending_redaction_preserves_following_fields() -> None:
    document = parse_text(
        "Invoice\nTotal: $5.00\n#\nMerchant: Café\nTotal: $10.00",
        source="client_ocr",
    )

    assert document.merchant == "Café"
    assert document.original_amount == Decimal("5.00")


def test_rmb_total_wins_over_subtotal_and_normalizes_currency() -> None:
    document = parse_text(
        "Cafe\nInvoice\nSubtotal: RMB 5.00\nTotal: RMB 20.00",
        source="client_ocr",
    )

    assert document.original_amount == Decimal("20.00")
    assert document.currency == "CNY"


def test_invalid_party_size_and_unrepresentable_amount_are_ignored() -> None:
    document = parse_text(
        "Cafe\nReceipt\nGuests: 0\nTotal: $" + ("9" * 100) + ".00",
        source="client_ocr",
    )

    assert document.party_size is None
    assert document.original_amount is None


def test_overlong_party_size_is_ignored() -> None:
    document = parse_text(
        "Cafe\nReceipt\nGuests: " + ("9" * 5000) + "\nTotal: $10.00",
        source="client_ocr",
    )

    assert document.party_size is None
    assert document.original_amount == Decimal("10.00")


def test_unparseable_amount_and_absent_merchant_are_not_invented() -> None:
    document = parse_text("Invoice\n\nTotal: pending\nDate: 2026-10-09", source="pdf")

    assert document.doc_role == "invoice"
    assert document.original_amount is None
    assert document.merchant is None


def test_time_without_meridiem_is_recognized() -> None:
    document = parse_text("Invoice\nTime: 09:15", source="pdf")

    assert document.occurred_time is not None and document.occurred_time.hour == 9


def test_invalid_date_or_time_is_not_extracted() -> None:
    for text in (
        "Invoice\nDate: 2026-99-99\nTotal: $1.00",
        "Invoice\nTime: 13:00 PM\nTotal: $1.00",
        "Invoice\nTime: 24:00\nTotal: $1.00",
        "Invoice\nTime: 10:60\nTotal: $1.00",
    ):
        document = parse_text(text, source="pdf")
        assert document.occurred_on is None and document.occurred_time is None


def test_trip_dates_fall_back_to_ordered_dates() -> None:
    document = parse_text("Trip Application\n2026-10-09\n2026-10-11", source="pdf")
    repeated = parse_text("Trip Application\n2026-10-09\n2026-10-09", source="pdf")

    assert document.trip_start_date == date(2026, 10, 9)
    assert document.trip_end_date == date(2026, 10, 11)
    assert repeated.trip_end_date is None


def _pdf(text: str) -> bytes:
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), text)
        return document.tobytes()


@pytest.mark.parametrize(
    ("text", "role", "amount"),
    [
        ("Uber\nReceipt\nTotal: $24.50", "payment_receipt", Decimal("24.50")),
        ("Grab\nInvoice\nTotal: SGD 21.23", "invoice", Decimal("21.23")),
    ],
)
def test_extract_generated_pdf(text: str, role: str, amount: Decimal) -> None:
    result = DeterministicExtractor().extract(
        ExtractionInput(mime_type="application/pdf", content=_pdf(text))
    )

    assert result is not None
    assert result.source == "pdf"
    assert result.doc_role == role
    assert result.original_amount == amount


def test_invalid_and_empty_pdfs_return_none() -> None:
    extractor = DeterministicExtractor()
    invalid = ExtractionInput(mime_type="application/pdf", content=b"not a pdf")
    scanned = ExtractionInput(mime_type="application/pdf", content=_pdf(""))

    assert extractor.extract(invalid) is None
    assert extractor.extract(scanned) is None


@pytest.mark.parametrize(
    "ocr",
    [
        {"lines": [{"text": "Invoice"}, {"text": "Total: USD 10.00"}]},
        [{"text": "Invoice"}, {"text": "Total: USD 10.00"}],
    ],
)
def test_extract_client_ocr_shapes(ocr: dict[str, object] | list[object]) -> None:
    result = DeterministicExtractor().extract(
        ExtractionInput(mime_type="image/jpeg", client_ocr_json=ocr)
    )

    assert result is not None
    assert result.doc_role == "invoice"
    assert result.original_amount == Decimal("10.00")
    assert result.source == "client_ocr"


def test_ocr_mean_empty_lines_and_confidence_scaling() -> None:
    extractor = DeterministicExtractor()
    result = extractor.extract(
        ExtractionInput(
            mime_type="image/png",
            client_ocr_json={
                "lines": [
                    {"text": "Cafe", "confidence": 0.4},
                    {"text": "Receipt", "confidence": 0.8},
                    {"text": "Total: $10.00", "confidence": 0.6},
                ]
            },
        )
    )
    empty = extractor.extract(
        ExtractionInput(
            mime_type="image/png",
            client_ocr_json={"lines": [{"text": "  "}, {"text": ""}]},
        )
    )

    assert result is not None
    assert result.confidence == 0.45
    assert empty is None


def test_oversized_integer_ocr_confidence_defaults_without_failing() -> None:
    result = DeterministicExtractor().extract(
        ExtractionInput(
            mime_type="image/png",
            client_ocr_json={
                "lines": [
                    {"text": "Cafe", "confidence": 10**10000},
                    {"text": "Invoice"},
                    {"text": "Date: 2026-10-09"},
                    {"text": "Total: USD 10.00"},
                ]
            },
        )
    )

    assert result is not None
    assert result.merchant == "Cafe"
    assert result.confidence == 1.0


@pytest.mark.parametrize(
    "ocr",
    [
        {"lines": "malformed"},
        {
            "lines": [
                None,
                {"text": "Cafe", "confidence": 2},
                {"text": "Invoice", "confidence": 0.5},
                {"text": "Date: 2026-10-09", "confidence": 0.5},
                {"text": "Total: $10.00", "confidence": 0.5},
            ]
        },
    ],
)
def test_malformed_ocr_lines_are_ignored_or_defaulted(
    ocr: dict[str, object],
) -> None:
    result = DeterministicExtractor().extract(
        ExtractionInput(mime_type="image/jpeg", client_ocr_json=ocr)
    )

    if ocr["lines"] == "malformed":
        assert result is None
    else:
        assert result is not None
        assert result.confidence == 0.62


def test_unhandled_input_returns_none() -> None:
    source = ExtractionInput(mime_type="application/octet-stream", content=b"x")
    assert DeterministicExtractor().extract(source) is None


def test_model_rejects_invalid_sensitive_or_amount_values() -> None:
    for field, value in (
        ("currency", "US"),
        ("card_last4", "4111111111111111"),
        ("original_amount", 1.25),
        ("original_amount", "not money"),
        ("tip_amount", "NaN"),
    ):
        with pytest.raises(ValidationError):
            ExtractedDocument(
                doc_role="unknown", confidence=0, source="pdf", **{field: value}
            )


def test_model_rejects_invalid_airport_codes() -> None:
    for field, value in (
        ("airport_iata", "LA"),
        ("airport_iata", "LAX1"),
        ("airport_iata", "lax"),
        ("destination_airports", ["LAX", "HKG1"]),
    ):
        with pytest.raises(ValidationError):
            ExtractedDocument(
                doc_role="unknown", confidence=0, source="pdf", **{field: value}
            )


def test_extracted_document_rejects_extra_fields() -> None:
    with pytest.raises(ValueError):
        ExtractedDocument(
            doc_role="unknown",
            confidence=0,
            source="pdf",
            unexpected="value",  # type: ignore[call-arg]
        )
