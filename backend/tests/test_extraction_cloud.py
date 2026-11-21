import json
from decimal import Decimal

import httpx
import pymupdf
import pytest

from app.extraction.cloud import (
    CloudAuditRecord,
    CloudExtractor,
    GatewayCloudClient,
    LayeredExtractor,
)
from app.extraction.models import ExtractionInput
from app.extraction.tier1 import DeterministicExtractor

TOKEN = "test-service-token"
FIELDS = {"doc_role": "invoice", "original_amount": "12.34"}
OCR = {"lines": [{"text": "Invoice\nTax Number: 123-45-6789\nCard: 4111111111111111"}]}
_VALID_RESPONSE = b'{"fields":{},"usage":{"total_tokens":0}}'
_Audits = list[CloudAuditRecord]


class _CloseFailure(httpx.ByteStream):
    def close(self) -> None:
        raise httpx.ReadError("mock close failure")


def _require(condition: bool) -> None:
    assert condition, "cloud extraction assertion failed"


def _client(handler: httpx.MockTransport, audits: _Audits) -> GatewayCloudClient:
    return GatewayCloudClient(
        *("https://gateway.invalid", TOKEN, "test-model", handler),
        audit_sink=audits.append,
    )


def _response(fields: object = FIELDS, tokens: int = 7) -> httpx.Response:
    payload = json.dumps({"fields": fields, "usage": {"total_tokens": tokens}})
    return httpx.Response(200, content=payload.encode())


def test_text_request_is_authenticated_redacted_and_audited() -> None:
    audits: list[CloudAuditRecord] = []
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _response()

    transport = httpx.MockTransport(respond)
    extractor = CloudExtractor(_client(transport, audits))
    result = extractor.extract(
        ExtractionInput(mime_type="image/jpeg", client_ocr_json=OCR)
    )
    _require(
        result is not None
        and result.source == "cloud"
        and result.original_amount == Decimal("12.34")
    )
    request = requests[0]
    _require(
        request.headers["Authorization"] == f"Bearer {TOKEN}"
        and request.headers["X-Request-ID"] == audits[0].request_id
    )
    body = request.read().decode()
    _require("123-45-6789" not in body and "4111111111111111" not in body)
    _require(
        len(audits) == 1 and audits[0].status == "ok" and audits[0].total_tokens == 7
    )
    _require(TOKEN not in repr(_client(transport, audits)))
    _require(TOKEN not in json.dumps(audits[0].__dict__))
    _require("123-45-6789" not in json.dumps(audits[0].__dict__))


@pytest.mark.parametrize(
    ("statuses", "expected_calls", "expected_status"),
    [
        ([503, 503, 200], 2, "error"),
        ([503, 200], 2, "ok"),
        ([401, 200], 1, "error"),
        ([httpx.ReadTimeout], 2, "timeout"),
        ([httpx.ConnectError], 2, "error"),
        ([httpx.RemoteProtocolError], 1, "error"),
        ([httpx.Response(200, stream=_CloseFailure(_VALID_RESPONSE))], 1, "error"),
    ],
)
def test_retry_limit_and_http_errors(
    statuses: list[int | type[httpx.TransportError] | httpx.Response],
    expected_calls: int,
    expected_status: str,
) -> None:
    audits: list[CloudAuditRecord] = []
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        outcome = statuses[min(calls, len(statuses) - 1)]
        calls += 1
        if isinstance(outcome, int):
            return _response() if outcome == 200 else httpx.Response(outcome)
        if isinstance(outcome, httpx.Response):
            return outcome
        raise outcome("mock transport error", request=request)

    result = _client(httpx.MockTransport(respond), audits).complete(
        text="Invoice", image=None, mime_type="text/plain"
    )
    _require(
        calls == expected_calls
        and (result is not None) == (expected_status == "ok")
        and len(audits) == 1
        and audits[0].status == expected_status
    )


def test_timeout_oversize_and_invalid_payloads_fail_closed() -> None:
    audits: list[CloudAuditRecord] = []
    calls = 0

    def should_not_send(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response()

    limited = GatewayCloudClient(
        "https://gateway.invalid",
        TOKEN,
        "test-model",
        transport=httpx.MockTransport(should_not_send),
        max_input_bytes=1,
        audit_sink=audits.append,
    )
    _require(
        limited.complete(text="xx", image=None, mime_type="text/plain") is None
        and calls == 0
        and audits[-1].status == "rejected"
    )
    _require(
        limited.complete(text="\ud800", image=None, mime_type="text/plain") is None
        and calls == 0
        and len(audits) == 2
        and audits[-1].status == "rejected"
    )
    invalid_responses = [
        _response({"original_amount": 1.25}),
        _response({"doc_role": "unknown-role"}),
        _response({"merchant": "synthetic-" + "1" * 12}),
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"usage": {"total_tokens": 1}}),
    ]
    source = ExtractionInput(mime_type="image/jpeg", content=b"image")
    for response in invalid_responses:
        client = _client(httpx.MockTransport(lambda request, r=response: r), audits)
        _require(CloudExtractor(client).extract(source) is None)
    cloud = CloudExtractor(_client(httpx.MockTransport(should_not_send), audits))
    empty_image = ExtractionInput(mime_type="image/png", content=b"")
    _require(cloud.extract(empty_image) is None)
    pdf = ExtractionInput(mime_type="application/pdf", client_ocr_json=OCR)
    for content in (None, b""):
        _require(cloud.extract(pdf.model_copy(update={"content": content})) is None)


def _pdf() -> bytes:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Cafe\nInvoice\n2026-10-09\nUSD\nTotal: $12.34")
        return document.tobytes()


def test_layering_skips_cloud_on_high_confidence_tier1() -> None:
    audits: list[CloudAuditRecord] = []
    transport = httpx.MockTransport(lambda request: _response())
    tier2 = CloudExtractor(_client(transport, audits))
    layered = LayeredExtractor(DeterministicExtractor(), tier2)
    result = layered.extract(
        ExtractionInput(mime_type="application/pdf", content=_pdf())
    )
    assert result is not None and result.source == "pdf" and not audits


@pytest.mark.parametrize(
    "role", ["invoice", "payment_receipt", "trip_application", "irrelevant"]
)
def test_layering_cloud_vision_returns_document_roles(role: str) -> None:
    audits: list[CloudAuditRecord] = []
    fields = {"doc_role": role, "confidence": 0.8}
    transport = httpx.MockTransport(lambda request: _response(fields))
    tier2 = CloudExtractor(_client(transport, audits))
    layered = LayeredExtractor(DeterministicExtractor(), tier2)
    result = layered.extract(
        ExtractionInput(mime_type="image/png", content=b"synthetic-image")
    )
    _require(
        result is not None
        and result.doc_role == role
        and result.source == "cloud"
        and len(audits) == 1
    )


def test_low_confidence_falls_back_and_tier2_can_be_disabled() -> None:
    audits: list[CloudAuditRecord] = []
    failing = httpx.MockTransport(
        lambda request: _response({"doc_role": "invoice", "merchant": "\ud800"})
    )
    source = ExtractionInput(mime_type="image/png", client_ocr_json=OCR)
    tier1 = DeterministicExtractor()
    layered = LayeredExtractor(tier1, CloudExtractor(_client(failing, audits)))
    first = layered.extract(source)
    _require(first is not None and first.doc_role == "invoice")
    _require(audits[0].status == "ok")
    _require(LayeredExtractor(tier1).extract(source) == first)
