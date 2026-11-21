import base64
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, cast
from uuid import uuid4

import httpx
import pymupdf
from pydantic import SecretStr

from app.extraction.models import (
    DocumentExtractorProtocol,
    ExtractedDocument,
    ExtractionInput,
)
from app.extraction.parser import sanitize_text
from app.extraction.tier1 import _ocr_lines

AuditStatus = Literal["ok", "error", "timeout", "rejected"]
AttemptResult = tuple[dict[str, object] | None, int | None, AuditStatus, bool]
_LONG_NUMBER = re.compile(r"\d{12,}")


def _response_fields(response: httpx.Response) -> AttemptResult:
    if not response.is_success:
        return None, None, "error", response.status_code >= 500
    try:
        payload = response.json()
        fields, usage = payload["fields"], payload["usage"]
        tokens = usage["total_tokens"]
        if (
            not isinstance(fields, dict)
            or not isinstance(usage, dict)
            or not isinstance(tokens, int)
            or isinstance(tokens, bool)
            or tokens < 0
        ):
            raise ValueError
    except KeyError, TypeError, ValueError:
        return None, None, "error", False
    return cast(dict[str, object], fields), tokens, "ok", False


@dataclass(frozen=True)
class CloudAuditRecord:
    request_id: str
    model: str
    duration_ms: int
    total_tokens: int | None
    status: AuditStatus


class GatewayCloudClient:
    def __init__(
        self,
        base_url: str,
        service_token: SecretStr | str,
        model: str,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 10,
        max_attempts: int = 2,
        max_input_bytes: int = 200_000,
        audit_sink: Callable[[CloudAuditRecord], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_attempts = min(3, max(1, max_attempts))
        self.service_token = SecretStr(
            service_token.get_secret_value()
            if isinstance(service_token, SecretStr)
            else service_token
        )
        self.model = model
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.max_input_bytes = max_input_bytes
        self.audit_sink = audit_sink

    def _payload(
        self, text: str | None, image: bytes | None, mime_type: str
    ) -> tuple[dict[str, object] | None, AuditStatus]:
        try:
            size = len(text.encode("utf-8")) if text is not None else len(image or b"")
        except UnicodeError:
            return None, "rejected"
        if size > self.max_input_bytes or (text is None and image == b""):
            return None, "rejected"
        if text is not None:
            return {"mode": "text", "text": text}, "error"
        if image is None:
            return None, "error"
        return {
            "mode": "vision",  # Raw image not redacted; size checked.
            "mime_type": mime_type,
            "image_base64": base64.b64encode(image).decode("ascii"),
        }, "error"

    def _send_once(
        self, session: httpx.Client, body: dict[str, object], request_id: str
    ) -> AttemptResult:
        headers = {
            "Authorization": f"Bearer {self.service_token.get_secret_value()}",
            "X-Request-ID": request_id,
        }
        try:
            response = session.post(
                f"{self.base_url}/extract", json=body, headers=headers
            )
        except httpx.TimeoutException:
            return None, None, "timeout", True
        except httpx.ConnectError:
            return None, None, "error", True
        except Exception:
            return None, None, "error", False
        return _response_fields(response)

    def _send(
        self, body: dict[str, object], request_id: str
    ) -> tuple[dict[str, object] | None, int | None, AuditStatus]:
        last: AttemptResult = (None, None, "error", True)
        try:
            with httpx.Client(
                transport=self.transport, timeout=self.timeout_seconds
            ) as session:
                for _ in range(self.max_attempts):
                    last = self._send_once(session, body, request_id)
                    if last[2] == "ok" or not last[3]:
                        break
        except Exception:
            return None, None, "error"
        return last[0], last[1], last[2]

    def _audit(
        self,
        request_id: str,
        started: float,
        total_tokens: int | None,
        status: AuditStatus,
    ) -> None:
        if self.audit_sink is None:
            return
        try:
            self.audit_sink(
                CloudAuditRecord(
                    request_id,
                    self.model,
                    int((time.monotonic() - started) * 1000),
                    total_tokens,
                    status,
                )
            )
        except Exception:
            pass

    def complete(
        self, *, text: str | None, image: bytes | None, mime_type: str
    ) -> dict[str, object] | None:
        request_id = str(uuid4())
        started = time.monotonic()
        body, status = self._payload(text, image, mime_type)
        result, total_tokens, status = (
            self._send(body, request_id) if body is not None else (None, None, status)
        )
        self._audit(request_id, started, total_tokens, status)
        return result


@dataclass
class CloudExtractor(DocumentExtractorProtocol):
    client: GatewayCloudClient

    def _source_text(self, source: ExtractionInput) -> str | None:
        if source.mime_type == "application/pdf":
            if not source.content:
                return None
            try:
                with pymupdf.open(stream=source.content, filetype="pdf") as document:  # type: ignore[no-untyped-call]
                    text = "\n".join(
                        document.load_page(i).get_text()
                        for i in range(document.page_count)
                    )
            except Exception:
                return None
            return sanitize_text(text) if text.strip() else None
        parsed = _ocr_lines(source.client_ocr_json) if source.client_ocr_json else None
        return sanitize_text(parsed[0]) if parsed else None

    def _validated_document(
        self, fields: dict[str, object] | None
    ) -> ExtractedDocument | None:
        if fields is None:
            return None
        fields.update(source="cloud")
        fields.setdefault("confidence", 0.5)
        try:
            document = ExtractedDocument.model_validate(fields)
            serialized = document.model_dump_json()
        except TypeError, ValueError:
            return None
        return None if _LONG_NUMBER.search(serialized) else document

    def extract(self, source: ExtractionInput) -> ExtractedDocument | None:
        text = self._source_text(source)
        image = (
            source.content
            if text is None and source.mime_type.startswith("image/")
            else None
        )
        if text is None and image is None:
            return None
        return self._validated_document(
            self.client.complete(text=text, image=image, mime_type=source.mime_type)
        )


@dataclass
class LayeredExtractor(DocumentExtractorProtocol):
    tier1: DocumentExtractorProtocol
    tier2: DocumentExtractorProtocol | None = None
    min_confidence: float = field(default=0.6, kw_only=True)

    def extract(self, source: ExtractionInput) -> ExtractedDocument | None:
        first = self.tier1.extract(source)
        qualified = (
            first is not None
            and first.doc_role != "unknown"
            and first.confidence >= self.min_confidence
        )
        if qualified:
            return first
        return self.tier2.extract(source) or first if self.tier2 is not None else first
