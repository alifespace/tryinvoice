import re
from datetime import date, time
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

DocumentRole = Literal[
    "invoice", "payment_receipt", "trip_application", "irrelevant", "unknown"
]
ExtractionSource = Literal["pdf", "client_ocr", "cloud"]
_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}\Z")
_CARD_LAST4_PATTERN = re.compile(r"\d{4}\Z")
_AIRPORT_IATA_PATTERN = re.compile(r"[A-Z]{3}\Z")
_CENT = Decimal("0.01")


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_role: DocumentRole
    merchant: str | None = None
    occurred_on: date | None = None
    occurred_time: time | None = None
    currency: str | None = None
    original_amount: Decimal | None = None
    tip_amount: Decimal = Decimal("0.00")
    party_size: int | None = Field(default=None, ge=1)
    card_last4: str | None = None
    reference_no: str | None = None
    has_paid_marker: bool = False
    airport_iata: str | None = None
    trip_start_date: date | None = None
    trip_end_date: date | None = None
    destination_airports: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    source: ExtractionSource

    @field_validator("currency")
    @classmethod
    def currency_is_iso_code(cls, value: str | None) -> str | None:
        if value is not None and not _CURRENCY_PATTERN.fullmatch(value):
            raise ValueError("币种必须为三位大写代码。")
        return value

    @field_validator("card_last4")
    @classmethod
    def card_is_last_four_only(cls, value: str | None) -> str | None:
        if value is not None and not _CARD_LAST4_PATTERN.fullmatch(value):
            raise ValueError("银行卡字段仅允许四位数字。")
        return value

    @field_validator("airport_iata")
    @classmethod
    def airport_is_iata_code(cls, value: str | None) -> str | None:
        if value is not None and not _AIRPORT_IATA_PATTERN.fullmatch(value):
            raise ValueError("机场代码必须为三位大写字母。")
        return value

    @field_validator("destination_airports")
    @classmethod
    def destinations_are_iata_codes(cls, value: list[str]) -> list[str]:
        if any(not _AIRPORT_IATA_PATTERN.fullmatch(code) for code in value):
            raise ValueError("目的地机场代码必须为三位大写字母。")
        return value

    @field_validator("original_amount", "tip_amount", mode="before")
    @classmethod
    def amount_has_decimal_precision(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, float):
            raise ValueError("金额必须使用 Decimal、整数或文本输入。")
        try:
            amount = Decimal(str(value))
            if not amount.is_finite():
                raise ValueError("金额必须为有限数值。")
            return amount.quantize(_CENT)
        except Exception as error:
            raise ValueError("金额格式无效。") from error


class ExtractionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mime_type: str
    content: bytes | None = None
    client_ocr_json: dict[str, object] | list[object] | None = None


class DocumentExtractorProtocol(Protocol):
    def extract(self, source: ExtractionInput) -> ExtractedDocument | None: ...
