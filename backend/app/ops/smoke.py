import argparse
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    status_code: int | None
    detail: str


@dataclass
class SmokeReport:
    steps: list[StepResult]

    @property
    def ok(self) -> bool:
        return all(step.ok for step in self.steps)


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    token: str | None = None,
    body: dict[str, str] | None = None,
) -> httpx.Response | None:
    try:
        request = client.build_request(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"} if token else None,
            json=body,
        )
        if not token:
            request.headers.pop("Authorization", None)
        return client.send(request, auth=None)
    except httpx.HTTPError:
        return None


def _id(response: httpx.Response | None, list_only: bool = False) -> str | None:
    if response is None:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if list_only and not isinstance(body, list):
        return None
    item = body[0] if isinstance(body, list) and body else body
    item_id = item.get("id") if isinstance(item, dict) else None
    return item_id if isinstance(item_id, str) and item_id else None


def _result(
    name: str,
    response: httpx.Response | None,
    expected: int,
    valid: bool = True,
) -> StepResult:
    if response is None:
        return StepResult(name, False, None, "network_error")
    ok = response.status_code == expected and valid
    return StepResult(
        name, ok, response.status_code, "ok" if ok else str(response.status_code)
    )


def _preflight(client: httpx.Client, steps: list[StepResult]) -> bool:
    for name, path, expected in (
        ("health", "/api/v1/health", 200),
        ("unauthenticated", "/api/v1/batches", 401),
    ):
        response = _request(client, "GET", path)
        valid = response is not None
        if name == "health" and response is not None:
            try:
                valid = response.json() == {"status": "ok"}
            except ValueError:
                valid = False
        result = _result(name, response, expected, valid)
        steps.append(result)
        if not result.ok:
            names: tuple[str, ...] = ("templates", "batch_create", "batch_delete")
            if name == "health":
                names = ("unauthenticated", *names)
            steps.extend(StepResult(item, False, None, "skipped") for item in names)
            return False
    return True


def _template_id(
    client: httpx.Client, token: str, steps: list[StepResult]
) -> str | None:
    response = _request(client, "GET", "/api/v1/templates", token)
    template_id = _id(response, list_only=True)
    valid = bool(response and response.status_code == 200 and template_id)
    result = _result("templates", response, 200, valid)
    steps.append(result)
    if not result.ok or template_id is None:
        steps.extend(
            StepResult(name, False, None, "skipped")
            for name in ("batch_create", "batch_delete")
        )
        return None
    return template_id


def _delete_batch(
    client: httpx.Client, token: str, batch_id: str, steps: list[StepResult]
) -> None:
    response = _request(client, "DELETE", f"/api/v1/batches/{batch_id}", token)
    result = _result("batch_delete", response, 204)
    steps.append(
        result
        if result.ok
        else StepResult("batch_delete", False, result.status_code, "cleanup_failed")
    )


def _create_batch(
    client: httpx.Client, token: str, template_id: str, steps: list[StepResult]
) -> None:
    response = _request(
        client,
        "POST",
        "/api/v1/batches",
        token,
        {
            "template_id": template_id,
            "expense_type": "meal",
            "title": "smoke-synthetic",
            "batch_date": "2026-01-01",
        },
    )
    batch_id = (
        _id(response) if response is not None and response.status_code == 201 else None
    )
    try:
        steps.append(_result("batch_create", response, 201, batch_id is not None))
    finally:
        if batch_id is None:
            steps.append(StepResult("batch_delete", False, None, "skipped"))
        else:
            _delete_batch(client, token, batch_id, steps)


def run_smoke(client: httpx.Client, access_token: str | None) -> SmokeReport:
    steps: list[StepResult] = []
    if not _preflight(client, steps):
        return SmokeReport(steps)
    token = access_token.strip() if access_token else ""
    if not token:
        steps.append(StepResult("authenticated", False, None, "no token"))
    else:
        template_id = _template_id(client, token, steps)
        if template_id is not None:
            _create_batch(client, token, template_id, steps)
    return SmokeReport(steps)


def _valid_base_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        _ = parsed.port
    except ValueError:
        return False
    allowed = (
        parsed.scheme == "https"
        or parsed.scheme == "http"
        and host in {"localhost", "127.0.0.1"}
    )
    return bool(
        host
        and allowed
        and not parsed.username
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def main(
    argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None
) -> int:
    parser = argparse.ArgumentParser(description="离线可测的 TryInvoice 后端冒烟检查")
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args(argv)
    if not _valid_base_url(args.base_url):
        print("config: fail(invalid_base_url)\nresult: fail")
        return 2
    token = os.environ.get("SMOKE_ACCESS_TOKEN")
    with httpx.Client(
        base_url=args.base_url, timeout=30.0, transport=transport
    ) as client:
        report = run_smoke(client, token)
    for step in report.steps:
        status = (
            "skipped(no token)"
            if step.detail == "no token"
            else "ok"
            if step.ok
            else f"fail({step.status_code or step.detail})"
        )
        print(f"{step.name}: {status}")
    print(f"result: {'pass' if report.ok else 'fail'}")
    return 2 if not token or not token.strip() else 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
