import math
from collections.abc import Mapping
from typing import cast

import pymupdf

from app.extraction.models import (
    DocumentExtractorProtocol,
    ExtractedDocument,
    ExtractionInput,
)
from app.extraction.parser import parse_text


def _ocr_lines(value: dict[str, object] | list[object]) -> tuple[str, float] | None:
    raw_lines: object = value.get("lines") if isinstance(value, dict) else value
    if not isinstance(raw_lines, list):
        return None
    texts: list[str] = []
    confidences: list[float] = []
    for raw_line in raw_lines:
        parsed = _ocr_line(raw_line)
        if parsed is None:
            continue
        text, confidence = parsed
        texts.append(text)
        confidences.append(confidence)
    if not texts:
        return None
    return "\n".join(texts), sum(confidences) / len(confidences)


def _ocr_line(raw_line: object) -> tuple[str, float] | None:
    if not isinstance(raw_line, Mapping):
        return None
    line = cast(Mapping[str, object], raw_line)
    text = line.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    confidence = line.get("confidence", 1.0)
    if (
        isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and 0 <= confidence <= 1
        and math.isfinite(confidence)
    ):
        return text.strip(), float(confidence)
    return text.strip(), 1.0


class DeterministicExtractor(DocumentExtractorProtocol):
    def extract(self, source: ExtractionInput) -> ExtractedDocument | None:
        if source.mime_type == "application/pdf" and source.content:
            return self._extract_pdf(source.content)
        if source.mime_type.startswith("image/") and source.client_ocr_json is not None:
            parsed = _ocr_lines(source.client_ocr_json)
            if parsed is not None:
                text, confidence = parsed
                return parse_text(text, source="client_ocr", ocr_confidence=confidence)
        return None

    @staticmethod
    def _extract_pdf(content: bytes) -> ExtractedDocument | None:
        try:
            with pymupdf.open(  # type: ignore[no-untyped-call]
                stream=content, filetype="pdf"
            ) as document:
                text = "\n".join(page.get_text() for page in document)
        except Exception:
            return None
        if not text.strip():
            return None
        return parse_text(text, source="pdf")
