import re
import unicodedata
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.extraction.models import ExtractedDocument, ExtractionSource

_TRIP_WORDS = re.compile(
    r"出差申请|business\s+trip|travel\s+request|trip\s+application", re.IGNORECASE
)
_INVOICE_WORDS = re.compile(r"invoice|发票", re.IGNORECASE)
_RECEIPT_WORDS = re.compile(r"receipt|payment|paid|收据|付款", re.IGNORECASE)
_AMOUNT_LABEL = re.compile(
    r"(?<![A-Za-z])(?:total\s+paid|grand\s+total|total|amount|合计|总计|金额|应付)"
    r"(?![A-Za-z])\s*(?:due)?\s*[:：]?\s*",
    re.IGNORECASE,
)
_NUMBER = re.compile(
    r"(?P<prefix>US\$|HK\$|S\$|C\$|A\$|[$¥￥]|[A-Z]{3})?\s*"
    r"(?P<number>[+-]?\d[\d,]*(?:\.\d{1,2})?)"
    r"\s*(?P<suffix>[A-Z]{3})?",
    re.IGNORECASE,
)
_TIP_LABEL = re.compile(r"(?:tip|gratuity|小费)\s*[:：]?\s*", re.IGNORECASE)
_PARTY = re.compile(r"(?:guests?|party\s+of|人数)\s*[:：]?\s*(\d+)", re.IGNORECASE)
_CARD_MASKED = re.compile(r"(?:\*{2,}|x{2,})\s*(\d{4})\b", re.IGNORECASE)
_CARD_ENDING = re.compile(r"(?:ending\s+in|尾号)\s*(\d{4})\b", re.IGNORECASE)
_FULL_CARD = re.compile(
    r"(?<![0-9])(?:[0-9][ -]?){11,}[0-9](?:\.[0-9]{1,2})?(?![0-9])",
    re.ASCII,
)
_SEP_CLASS = r"\s._/\-"
_SEP = rf"[{_SEP_CLASS}]*"
_ID_SUFFIX = r"(?:(?:no|num[a-z]*|id|reg[a-z]*|identif[a-z]*|code)(?![A-Za-z0-9])|#)"
_TRIP_ID = re.compile(r"(?<![A-Za-z0-9_])trip +id(?![A-Za-z0-9_])", re.IGNORECASE)
_SENSITIVE_SUFFIX = re.compile(rf"[{_SEP_CLASS}:：#号码]*", re.ASCII)
_SENSITIVE_LABEL = re.compile(
    r"(?:(?<![A-Za-z0-9_])(?:"
    rf"(?:ssn|tin|ein|gstin)(?:(?![A-Za-z0-9_])|(?={_SEP}{_ID_SUFFIX}))(?:{_SEP}{_ID_SUFFIX})*|"
    rf"social{_SEP}security(?:{_SEP}{_ID_SUFFIX})*|"
    rf"national{_SEP}id(?:{_SEP}{_ID_SUFFIX})*|"
    rf"passport(?:{_SEP}{_ID_SUFFIX})*|"
    rf"id{_SEP}{_ID_SUFFIX}(?:{_SEP}{_ID_SUFFIX})*|"
    rf"(?:taxpayer|tax|vat|gst|hst|pst)(?:{_SEP}{_ID_SUFFIX})+)|"
    r"身份证(?:号码|号)?|证件(?:号码|号)|护照(?:号码|号)|"
    r"纳税人识别号|纳税人编号|统一社会信用代码|社会信用代码|增值税号|税务登记号|税号)",
    re.IGNORECASE | re.ASCII,
)
_SENSITIVE_ID_NUMBER = re.compile(
    r"(?<![0-9])(?:\d{3}-\d{2}-\d{4}|\d{2}-\d{7})(?![0-9])",
    re.ASCII,
)
_REFERENCE = re.compile(
    r"(?:invoice\s*(?:no\.?|number|#)|receipt\s*(?:no\.?|number|#)|"
    rf"order(?:\s*(?:no\.?|number|#))?|{_TRIP_ID.pattern}|流水号)"
    r"\s*[:#]?\s*([A-Z0-9-]{1,40})",
    re.IGNORECASE,
)
_DATE_PATTERNS = ("%Y-%m-%d|%Y/%m/%d|%d %b %Y|%d %B %Y|%b %d, %Y|%B %d, %Y").split("|")
_DATE_TEXT = re.compile(
    r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|"
    r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})\b"
)
_TIME_TEXT = re.compile(r"\b(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?\b", re.I)
_AIRPORT_CONTEXT = re.compile(r"airport|destination|机场", re.IGNORECASE)
_IATA = re.compile(r"\b([A-Z]{3})\b")
_MERCHANT_LABEL = re.compile(r"^(?:merchant|from)\s*:\s*(.+)$", re.IGNORECASE)
_NON_MERCHANT = re.compile(
    r"^(?:invoice|receipt|payment|paid|total|amount|tip|gratuity|"
    r"date|time|order|trip application|business trip|travel request|"
    r"airport|destination|start|end|return|guests?|party|card)\b",
    re.IGNORECASE,
)
_CURRENCY_CODES = re.compile(r"\b([A-Z]{3})\b")


def _find_amount(
    text: str, label: re.Pattern[str]
) -> tuple[Decimal | None, str | None]:
    for line in text.splitlines():
        match = label.search(line)
        if match is None:
            continue
        amount_match = _NUMBER.search(line, match.end())
        if amount_match is None:
            continue
        raw_amount = amount_match.group("number").replace(",", "")
        try:
            amount = Decimal(raw_amount).quantize(Decimal("0.01"))
        except InvalidOperation:
            continue
        token = amount_match.group("prefix") or amount_match.group("suffix")
        currency = _currency_from_token(token)
        return amount, currency
    return None, None


def _sanitize(text: str) -> str:
    normalized = re.sub(r"[^\S\r\n]", " ", unicodedata.normalize("NFKC", text))
    lines = normalized.splitlines()
    protected = _TRIP_ID.sub(lambda match: match.group()[:-1] + "_", "\n".join(lines))
    match = next(matches := _SENSITIVE_LABEL.finditer(protected), None)
    sanitized: list[str] = []
    redact_next = False
    offset = 0
    for line in lines:
        offset, line_end = offset + len(line) + 1, offset + len(line)
        if not line.strip():
            sanitized.append(line)
            continue
        cut = label_end = None
        while match is not None and match.start() < line_end:
            cut = max(0, match.start() - (line_end - len(line))) if cut is None else cut
            if match.end() <= line_end:
                label_end = match.end() - (line_end - len(line))
                match = next(matches, None)
            else:
                break
        next_sensitive = label_end is not None and bool(
            _SENSITIVE_SUFFIX.fullmatch(line[label_end:])
        )
        redact_current = redact_next
        redact_next = _redact_state(line, redact_current, next_sensitive)
        if cut is not None:
            line = line[:cut] + "[REDACTED]"
        line = "[REDACTED]" if redact_current else line
        sanitized.append(
            _SENSITIVE_ID_NUMBER.sub("[REDACTED]", _FULL_CARD.sub("[REDACTED]", line))
        )
    return "\n".join(sanitized)


sanitize_text = _sanitize


def _redact_state(line: str, pending: bool, sensitive: bool) -> bool:
    return sensitive or (pending and bool(_SENSITIVE_SUFFIX.fullmatch(line.strip())))


def _currency_from_token(token: str | None) -> str | None:
    if token is None:
        return None
    normalized = token.upper()
    return {
        "US$": "USD",
        "$": "USD",
        "HK$": "HKD",
        "S$": "SGD",
        "C$": "CAD",
        "A$": "AUD",
        "¥": "CNY",
        "￥": "CNY",
        "RMB": "CNY",
    }.get(normalized, normalized if len(normalized) == 3 else None)


def _find_currency(text: str, amount_currency: str | None) -> str | None:
    if amount_currency is not None:
        return amount_currency
    for symbol, currency in zip(
        "HK$ US$ S$ C$ A$ $ ¥ ￥".split(),
        "HKD USD SGD CAD AUD USD CNY CNY".split(),
        strict=True,
    ):
        if symbol in text:
            return currency
    for line in text.splitlines():
        if re.search(r"(?:currency|币种)\s*[:：]", line, re.IGNORECASE):
            if code := _CURRENCY_CODES.search(line):
                return _currency_from_token(code.group(1))
    return None


def _parse_date(value: str) -> date | None:
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    return None


def _dates(text: str) -> list[tuple[date, str]]:
    found: list[tuple[date, str]] = []
    for match in _DATE_TEXT.finditer(text):
        if parsed := _parse_date(match.group()):
            found.append((parsed, match.group()))
    return found


def _trip_dates(
    text: str, found: list[tuple[date, str]]
) -> tuple[date | None, date | None]:
    start: date | None = None
    end: date | None = None
    for line in text.splitlines():
        start_on_line = _labelled_trip_date(
            line, found, r"\b(start|depart|from)\b|开始|出发"
        )
        end_on_line = _labelled_trip_date(
            line, found, r"\b(end|return|until|to)\b|结束|返回", last=True
        )
        start, end = start_on_line or start, end_on_line or end
    if start is None and found:
        start = found[0][0]
    if end is None and len(found) > 1:
        end = found[1][0] if found[1][0] != start else None
    return start, end


def _labelled_trip_date(
    line: str,
    found: list[tuple[date, str]],
    labels: str,
    *,
    last: bool = False,
) -> date | None:
    line_dates = [parsed for parsed, raw in found if raw in line]
    if not line_dates or not re.search(labels, line, re.IGNORECASE):
        return None
    return line_dates[-1] if last else line_dates[0]


def _merchant(text: str) -> str | None:
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        label_match = _MERCHANT_LABEL.fullmatch(candidate)
        if label_match:
            value = label_match.group(1).strip()
            if value and not _is_non_merchant_line(value):
                return value[:120]
            continue
        if _is_non_merchant_line(candidate):
            continue
        return candidate[:120]
    return None


def _is_non_merchant_line(candidate: str) -> bool:
    return bool(
        "[REDACTED]" in candidate
        or _NON_MERCHANT.search(candidate)
        or any(
            pattern.search(candidate)
            for pattern in (_AMOUNT_LABEL, _DATE_TEXT, _CARD_MASKED, _CARD_ENDING)
        )
        or re.fullmatch(r"[\W\d_]+", candidate)
    )


def _airport_codes(text: str) -> tuple[str | None, list[str]]:
    codes: list[str] = []
    for line in text.splitlines():
        if not _AIRPORT_CONTEXT.search(line):
            continue
        codes.extend(match.group(1) for match in _IATA.finditer(line))
    unique = list(dict.fromkeys(codes))
    return (unique[0] if unique else None), unique


def _card_last4(text: str) -> str | None:
    for line in text.splitlines():
        masked = _CARD_MASKED.search(line)
        if masked:
            return masked.group(1)
        ending = _CARD_ENDING.search(line)
        if ending:
            return ending.group(1)
        if not re.search(r"card|credit|银行卡", line, re.IGNORECASE):
            continue
        full_card = _FULL_CARD.search(line)
        if full_card:
            digits = re.sub(r"\D", "", full_card.group().split(".")[0])
            return digits[-4:]
    return None


def _reference_no(text: str) -> str | None:
    for line in text.splitlines():
        if match := _REFERENCE.search(line):
            return match.group(1)[:40]
    return None


def _role_for(
    text: str,
    amount: Decimal | None,
) -> Literal["invoice", "payment_receipt", "trip_application", "irrelevant", "unknown"]:
    if _TRIP_WORDS.search(text):
        return "trip_application"
    if _INVOICE_WORDS.search(text):
        return "invoice"
    if _RECEIPT_WORDS.search(text):
        return "payment_receipt"
    if text.strip() and amount is None:
        return "irrelevant"
    return "unknown"


def _confidence(
    role: str,
    merchant: str | None,
    occurred_on: date | None,
    amount: Decimal | None,
    currency: str | None,
    trip_start: date | None,
    trip_end: date | None,
    ocr_confidence: float,
) -> float:
    if role in ("invoice", "payment_receipt"):
        score = (
            sum(
                value is not None for value in (merchant, occurred_on, amount, currency)
            )
            / 4
        )
    elif role == "trip_application":
        score = (int(trip_start is not None) + int(trip_end is not None)) / 2
    else:
        score = 1.0 if role == "irrelevant" else 0.0
    return round(score * ocr_confidence, 2)


def parse_text(
    text: str,
    *,
    source: ExtractionSource,
    ocr_confidence: float = 1.0,
) -> ExtractedDocument:
    card_last4 = _card_last4(text)
    cleaned = _sanitize(text.strip())
    amount, amount_currency = _find_amount(cleaned, _AMOUNT_LABEL)
    tip_amount, _ = _find_amount(cleaned, _TIP_LABEL)
    role = _role_for(cleaned, amount)
    merchant = _merchant(cleaned)
    found_dates = _dates(cleaned)
    occurred_on = found_dates[0][0] if found_dates else None
    occurred_time = _parse_time(cleaned)
    currency = _find_currency(cleaned, amount_currency)
    trip_start, trip_end = _trip_dates(cleaned, found_dates)
    airport_iata, destinations = _airport_codes(cleaned)
    party_match = _PARTY.search(cleaned)
    party_size = _party_size(party_match.group(1)) if party_match else None
    party_size = party_size or None
    paid = bool(re.search(r"\bpaid\b|已付款", cleaned, re.IGNORECASE))
    confidence = _confidence(
        role,
        merchant,
        occurred_on,
        amount,
        currency,
        trip_start,
        trip_end,
        ocr_confidence,
    )
    return ExtractedDocument(
        doc_role=role,
        merchant=merchant,
        occurred_on=occurred_on,
        occurred_time=occurred_time,
        currency=currency,
        original_amount=amount,
        tip_amount=tip_amount or Decimal("0.00"),
        party_size=party_size,
        card_last4=card_last4,
        reference_no=_reference_no(cleaned),
        has_paid_marker=paid,
        airport_iata=airport_iata,
        trip_start_date=trip_start,
        trip_end_date=trip_end,
        destination_airports=destinations,
        confidence=confidence,
        source=source,
    )


def _parse_time(text: str) -> time | None:
    match = _TIME_TEXT.search(text)
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    meridiem = (match.group(3) or "").upper()
    if meridiem and not 1 <= hour <= 12:
        return None
    hour = hour % 12 + (12 if meridiem == "PM" else 0) if meridiem else hour
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _party_size(value: str) -> int | None:
    if len(value) > 3:
        return None
    try:
        return int(value)
    except ValueError:
        return None
