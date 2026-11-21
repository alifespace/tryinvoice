import runpy
import sys

import httpx
import pytest

import app.ops.smoke as smoke
from app.ops.smoke import _valid_base_url, main, run_smoke


def _response(req: httpx.Request, status: int, body=None, raw=False) -> httpx.Response:
    return (
        httpx.Response(status, content=b"{", request=req)
        if raw
        else httpx.Response(status, json=body, request=req)
    )


def _client(handler):
    return httpx.Client(
        base_url="https://example.test",
        headers={"Authorization": "Bearer client-default"},
        transport=httpx.MockTransport(handler),
    )


def _successful_handler(requests: list[httpx.Request], error_path: str | None = None):
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == error_path:
            raise httpx.ConnectError("TOKEN_SENTINEL BODY_SENTINEL", request=request)
        if request.url.path.endswith("/health"):
            return _response(request, 200, {"status": "ok"})
        if request.url.path.endswith("/batches") and request.method == "GET":
            return _response(request, 401, {"secret": "BODY_SENTINEL"})
        if request.url.path.endswith("/templates"):
            return _response(request, 200, [{"id": "template-1"}])
        if request.method == "POST":
            return _response(request, 201, {"id": "batch-1"})
        return _response(request, 204)

    return respond


def test_full_smoke_uses_expected_sequence_and_auth_scope(monkeypatch, capsys) -> None:
    monkeypatch.setenv("SMOKE_ACCESS_TOKEN", "synthetic-token")
    requests: list[httpx.Request] = []
    status = main(
        ["--base-url", "https://example.test"],
        transport=httpx.MockTransport(_successful_handler(requests)),
    )
    assert status == 0
    assert [r.method for r in requests] == ["GET", "GET", "GET", "POST", "DELETE"]
    assert requests[3].read() == (
        b'{"template_id":"template-1","expense_type":"meal",'
        b'"title":"smoke-synthetic","batch_date":"2026-01-01"}'
    )
    assert [request.headers.get("authorization") for request in requests] == [
        None,
        None,
        "Bearer synthetic-token",
        "Bearer synthetic-token",
        "Bearer synthetic-token",
    ]
    assert requests[-1].url.path == "/api/v1/batches/batch-1"
    output = capsys.readouterr().out
    assert "synthetic-token" not in output
    assert "BODY_SENTINEL" not in output


def test_health_failure_skips_all_remaining_requests() -> None:
    requests: list[httpx.Request] = []
    with _client(
        lambda request: requests.append(request) or _response(request, 200, raw=True)
    ) as client:
        report = run_smoke(client, "token")
    assert not report.ok and len(requests) == 1
    assert [step.detail for step in report.steps[1:]] == ["skipped"] * 4


def test_unauthenticated_endpoint_rejects_200_even_with_token() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/health"):
            return _response(request, 200, {"status": "ok"})
        if request.method == "GET" and request.url.path.endswith("/batches"):
            return _response(request, 200)
        if request.url.path.endswith("/templates"):
            return _response(request, 200, [{"id": "template-1"}])
        if request.method == "POST":
            return _response(request, 201, {"id": "batch-1"})
        return _response(request, 204)

    with _client(respond) as client:
        report = run_smoke(client, "synthetic-token")
    assert not report.ok
    assert not report.steps[1].ok and report.steps[1].status_code == 200
    assert [step.detail for step in report.steps[2:]] == ["skipped"] * 3
    assert len(requests) == 2 and requests[1].url.path == "/api/v1/batches"
    assert all("authorization" not in request.headers for request in requests)


def test_missing_token_returns_two_without_authenticated_requests(
    monkeypatch, capsys
) -> None:
    monkeypatch.delenv("SMOKE_ACCESS_TOKEN", raising=False)
    requests: list[httpx.Request] = []
    status = main(
        ["--base-url", "https://example.test"],
        transport=httpx.MockTransport(_successful_handler(requests)),
    )
    assert status == 2 and len(requests) == 2
    assert "authenticated: skipped(no token)" in capsys.readouterr().out


def test_create_is_followed_by_delete_even_when_cleanup_fails(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/health"):
            return _response(request, 200, {"status": "ok"})
        if request.method == "GET" and request.url.path.endswith("/batches"):
            return _response(request, 401)
        if request.url.path.endswith("/templates"):
            return _response(request, 200, [{"id": "template-1"}])
        if request.method == "POST":
            return _response(request, 201, {"id": "batch-1"})
        return _response(request, 500, {"secret": "BODY_SENTINEL"})

    with _client(respond) as client:
        report = run_smoke(client, "token")
        requests.clear()
        original = smoke._result

        def fail_create(name, *args):
            assert name != "batch_create"
            return original(name, *args)

        monkeypatch.setattr(smoke, "_result", fail_create)
        with pytest.raises(AssertionError):
            run_smoke(client, "token")
    assert [r.method for r in requests][-2:] == ["POST", "DELETE"]
    assert report.steps[-1].detail == "cleanup_failed"
    assert not report.ok


def test_network_error_detail_does_not_include_exception_text() -> None:
    with _client(_successful_handler([], "/api/v1/templates")) as client:
        report = run_smoke(client, "token")
    assert report.steps[2].detail == "network_error"
    assert "SENTINEL" not in repr(report)


def test_module_entry_rejects_insecure_url_without_client(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["smoke", "--base-url", "http://example.com"])
    monkeypatch.setattr(
        smoke.httpx, "Client", lambda **kwargs: pytest.fail("unexpected client")
    )
    with pytest.raises(SystemExit) as error:
        runpy.run_path(smoke.__file__, run_name="__main__")
    assert error.value.code == 2 and not _valid_base_url("http://localhost:99999")
    assert capsys.readouterr().out == "config: fail(invalid_base_url)\nresult: fail\n"
