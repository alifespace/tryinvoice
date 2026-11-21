import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app import auth_jwks
from app.api.deps import _user_id_from_payload, get_current_user_id
from app.config import Settings

_ISSUER = "https://synthetic.supabase.co/auth/v1"
_AUDIENCE = "authenticated"
_SECRET = "synthetic-legacy-secret-for-tests-only"


@pytest.fixture(autouse=True)
def reset_cache() -> None:
    auth_jwks.reset_jwks_cache()


@pytest.fixture
def jwks_fetcher(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Any], list[tuple[str, float]]]:
    responses: list[Any] = []
    calls: list[tuple[str, float]] = []

    def fetcher(url: str, timeout: float) -> list[dict[str, Any]]:
        calls.append((url, timeout))
        response = responses.pop(0) if len(responses) > 1 else responses[0]
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(auth_jwks, "_fetch_jwks", fetcher)
    return responses, calls


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Settings:
    values = {
        "supabase_url": "https://synthetic.supabase.co",
        "supabase_jwt_issuer": _ISSUER,
        "supabase_jwt_secret": _SECRET,
        **overrides,
    }
    settings = Settings(_env_file=None, **values)
    monkeypatch.setattr("app.api.deps.get_settings", lambda: settings)
    return settings


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "headers": [],
            "method": "GET",
            "path": "/",
            "query_string": b"",
        }
    )


def _claims(user_id: UUID, **changes: Any) -> dict[str, Any]:
    return {
        "sub": str(user_id),
        "aud": _AUDIENCE,
        "iss": _ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        **changes,
    }


def _ec_key(key_id: str) -> tuple[ec.EllipticCurvePrivateKey, dict[str, Any]]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": key_id, "alg": "ES256", "use": "sig"})
    return private_key, public_jwk


def _es256_token(
    user_id: UUID,
    private_key: ec.EllipticCurvePrivateKey,
    key_id: str,
    **claims: Any,
) -> str:
    return jwt.encode(
        _claims(user_id, **claims),
        private_key,
        algorithm="ES256",
        headers={"kid": key_id},
    )


def _forged_hs256_token(user_id: UUID, jwks_text: str) -> str:
    header = jwt.utils.base64url_encode(b'{"alg":"HS256","typ":"JWT"}')
    claims = _claims(user_id)
    claims["exp"] = int(claims["exp"].timestamp())
    payload = jwt.utils.base64url_encode(
        json.dumps(claims, separators=(",", ":")).encode()
    )
    signing_input = header + b"." + payload
    signature = hmac.new(jwks_text.encode(), signing_input, hashlib.sha256).digest()
    return b".".join((header, payload, jwt.utils.base64url_encode(signature))).decode()


def _authenticate(token: str) -> UUID:
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    return get_current_user_id(_request(), credentials)


@pytest.mark.parametrize(
    ("overrides", "expected_url"),
    [
        ({}, "https://synthetic.supabase.co/auth/v1/.well-known/jwks.json"),
        (
            {
                "supabase_jwks_url": "https://keys.synthetic/jwks",
                "supabase_jwt_issuer": None,
            },
            "https://keys.synthetic/jwks",
        ),
    ],
)
def test_valid_es256_token_uses_cached_jwks(
    monkeypatch: pytest.MonkeyPatch,
    jwks_fetcher: tuple[list[Any], list[tuple[str, float]]],
    overrides: dict[str, Any],
    expected_url: str,
) -> None:
    user_id = uuid4()
    private_key, public_jwk = _ec_key("current")
    responses, calls = jwks_fetcher
    responses.append([public_jwk])
    _settings(monkeypatch, **overrides)
    token = _es256_token(user_id, private_key, "current")
    assert _authenticate(token) == user_id
    assert _authenticate(token) == user_id
    assert calls == [(expected_url, 3.0)]


def test_missing_jwks_or_issuer_returns_503(
    monkeypatch: pytest.MonkeyPatch,
    jwks_fetcher: tuple[list[Any], list[tuple[str, float]]],
) -> None:
    private_key, public_jwk = _ec_key("unconfigured")
    responses, _ = jwks_fetcher
    responses.append([public_jwk])
    token = _es256_token(uuid4(), private_key, "unconfigured")
    for config in (
        {"supabase_url": None, "supabase_jwks_url": None, "supabase_jwt_issuer": None},
        {
            "supabase_url": None,
            "supabase_jwks_url": "https://keys.synthetic/jwks",
            "supabase_jwt_issuer": None,
        },
    ):
        auth_jwks.reset_jwks_cache()
        _settings(monkeypatch, **config)
        with pytest.raises(HTTPException) as error:
            _authenticate(token)
        assert error.value.status_code == 503


def test_es256_requires_kid_before_fetch(
    monkeypatch: pytest.MonkeyPatch,
    jwks_fetcher: tuple[list[Any], list[tuple[str, float]]],
) -> None:
    private_key, _ = _ec_key("missing-kid")
    _, calls = jwks_fetcher
    _settings(monkeypatch)
    token = jwt.encode(_claims(uuid4()), private_key, algorithm="ES256")
    with pytest.raises(HTTPException) as error:
        _authenticate(token)
    assert error.value.status_code == 401
    assert calls == []


def test_fetcher_validates_status_and_jwks_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, public_jwk = _ec_key("fetched")
    request = httpx.Request("GET", "https://keys.synthetic/jwks")
    responses = [
        httpx.Response(200, json={"keys": [public_jwk, "ignored"]}, request=request),
        httpx.Response(200, json={"keys": "invalid"}, request=request),
        httpx.Response(201, json={"keys": [public_jwk]}, request=request),
        httpx.Response(503, request=request),
    ]
    monkeypatch.setattr(auth_jwks.httpx, "get", lambda url, timeout: responses.pop(0))
    assert auth_jwks._fetch_jwks(request.url, 3.0) == [public_jwk]
    with pytest.raises(ValueError):
        auth_jwks._fetch_jwks(request.url, 3.0)
    with pytest.raises(httpx.HTTPStatusError):
        auth_jwks._fetch_jwks(request.url, 3.0)
    with pytest.raises(httpx.HTTPStatusError):
        auth_jwks._fetch_jwks(request.url, 3.0)


def test_non_string_subject_is_rejected() -> None:
    with pytest.raises(HTTPException) as error:
        _user_id_from_payload({"sub": 123})
    assert error.value.status_code == 401


def test_unknown_kid_refreshes_once_for_key_rotation(
    monkeypatch: pytest.MonkeyPatch,
    jwks_fetcher: tuple[list[Any], list[tuple[str, float]]],
) -> None:
    user_id = uuid4()
    old_private, old_jwk = _ec_key("old")
    new_private, new_jwk = _ec_key("new")
    responses, calls = jwks_fetcher
    responses.extend(([old_jwk], [new_jwk]))
    _settings(monkeypatch)
    assert _authenticate(_es256_token(user_id, new_private, "new")) == user_id
    assert len(calls) == 2


@pytest.mark.parametrize("mode", ["unknown", "malformed"])
def test_unknown_or_malformed_key_after_refresh_is_401(
    monkeypatch: pytest.MonkeyPatch,
    jwks_fetcher: tuple[list[Any], list[tuple[str, float]]],
    mode: str,
) -> None:
    key_id = "known" if mode == "malformed" else "unknown"
    private_key, public_jwk = _ec_key("known")
    response = [{"kid": "known", "kty": "EC"}] if mode == "malformed" else [public_jwk]
    responses, calls = jwks_fetcher
    responses.append(response)
    _settings(monkeypatch)
    token = _es256_token(uuid4(), private_key, key_id)
    with pytest.raises(HTTPException) as error:
        _authenticate(token)
    assert error.value.status_code == 401
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}
    assert len(calls) == 2


@pytest.mark.parametrize(
    "case",
    [
        "expired",
        "issuer",
        "audience",
        "audience_array",
        "missing_sub",
        "invalid_sub",
        "none",
        "hs256",
    ],
)
def test_invalid_claims_and_algorithms_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    jwks_fetcher: tuple[list[Any], list[tuple[str, float]]],
    case: str,
) -> None:
    private_key, public_jwk = _ec_key("key")
    responses, calls = jwks_fetcher
    responses.append([public_jwk])
    _settings(monkeypatch)
    user_id = uuid4()
    changes = {
        "expired": {"exp": datetime.now(UTC) - timedelta(minutes=1)},
        "issuer": {"iss": "https://wrong.example/auth/v1"},
        "audience": {"aud": "wrong"},
        "audience_array": {"aud": [_AUDIENCE]},
        "invalid_sub": {"sub": "not-a-uuid"},
    }.get(case, {})
    claims = _claims(user_id, **changes)
    if case == "missing_sub":
        claims.pop("sub")
    token = (
        jwt.encode(claims, "", algorithm="none")
        if case == "none"
        else _forged_hs256_token(user_id, json.dumps({"keys": [public_jwk]}))
        if case == "hs256"
        else jwt.encode(claims, private_key, algorithm="ES256", headers={"kid": "key"})
    )
    with pytest.raises(HTTPException) as error:
        _authenticate(token)
    assert error.value.status_code == 401
    if case in {"none", "hs256"}:
        assert calls == []


def test_legacy_hs256_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    token = jwt.encode(_claims(user_id), _SECRET, algorithm="HS256")
    _settings(monkeypatch, supabase_allow_legacy_hs256=True)
    assert _authenticate(token) == user_id
    _settings(monkeypatch)
    with pytest.raises(HTTPException) as error:
        _authenticate(token)
    assert error.value.status_code == 401


@pytest.mark.parametrize("failure", ["timeout", "server", "json"])
def test_fetch_failure_without_cache_returns_503(
    monkeypatch: pytest.MonkeyPatch,
    jwks_fetcher: tuple[list[Any], list[tuple[str, float]]],
    failure: str,
) -> None:
    private_key, public_jwk = _ec_key("key")
    request = httpx.Request("GET", "https://keys.synthetic/jwks")
    responses, _ = jwks_fetcher
    responses.append(
        httpx.ReadTimeout("synthetic timeout")
        if failure == "timeout"
        else httpx.HTTPStatusError(
            "synthetic status",
            request=request,
            response=httpx.Response(503, request=request),
        )
        if failure == "server"
        else ValueError("synthetic invalid JSON")
    )
    _settings(monkeypatch)
    token = _es256_token(uuid4(), private_key, "key")
    with pytest.raises(HTTPException) as error:
        _authenticate(token)
    assert error.value.status_code == 503
    assert error.value.detail == "身份验证服务暂不可用"
    assert "synthetic" not in error.value.detail


def test_stale_cache_is_used_on_fetch_error_within_two_ttls(
    monkeypatch: pytest.MonkeyPatch,
    jwks_fetcher: tuple[list[Any], list[tuple[str, float]]],
) -> None:
    private_key, public_jwk = _ec_key("key")
    times = iter([100.0, 115.0, 115.0, 119.0, 121.0])
    monkeypatch.setattr(auth_jwks.time, "monotonic", lambda: next(times))
    responses, _ = jwks_fetcher
    responses.append([public_jwk])
    settings = _settings(monkeypatch, supabase_jwks_cache_seconds=10)
    token = _es256_token(uuid4(), private_key, "key")
    assert _authenticate(token)

    responses[0] = httpx.TimeoutException("synthetic timeout")
    assert _authenticate(token)
    assert settings.supabase_jwks_cache_seconds == 10
    with pytest.raises(HTTPException) as error:
        _authenticate(token)
    assert error.value.status_code == 503


def test_refresh_throttle_prevents_repeated_unknown_kid_fetches(
    monkeypatch: pytest.MonkeyPatch,
    jwks_fetcher: tuple[list[Any], list[tuple[str, float]]],
) -> None:
    private_key, public_jwk = _ec_key("known")
    responses, calls = jwks_fetcher
    responses.append([public_jwk])
    _settings(monkeypatch)
    token = _es256_token(uuid4(), private_key, "unknown")
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            _authenticate(token)
        assert error.value.status_code == 401
    assert len(calls) == 2


def test_refresh_throttle_does_not_use_cache_beyond_two_ttls(
    monkeypatch: pytest.MonkeyPatch,
    jwks_fetcher: tuple[list[Any], list[tuple[str, float]]],
) -> None:
    monkeypatch.setattr(auth_jwks.time, "monotonic", lambda: 130.0)
    settings = _settings(monkeypatch, supabase_jwks_cache_seconds=10)
    auth_jwks._cache = auth_jwks._JwksCache(keys=[], fetched_at=109.0)
    auth_jwks._last_forced_refresh = 120.0

    with pytest.raises(auth_jwks.JwksUnavailable):
        auth_jwks.get_jwks(settings, force_refresh=True)
    assert jwks_fetcher[1] == []


def test_failure_log_and_response_do_not_include_token_or_jwks(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_key, public_jwk = _ec_key("key")
    token = _es256_token(uuid4(), private_key, "key")
    jwks_text = json.dumps([public_jwk])
    url = "https://keys.synthetic/jwks?synthetic=query"

    def failed_get(request_url: str, timeout: float) -> httpx.Response:
        for logger_name in ("httpx", "httpcore.http11", "httpcore.connection"):
            logging.getLogger(logger_name).debug("request metadata %s", jwks_text)
        logging.getLogger("httpcore.proxy").debug("request metadata %s", jwks_text)
        logging.getLogger("httpcore.socks").debug("request metadata %s", jwks_text)
        raise httpx.ConnectError(jwks_text, request=httpx.Request("GET", request_url))

    monkeypatch.setattr(auth_jwks.httpx, "get", failed_get)
    _settings(monkeypatch, supabase_jwks_url=url)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(HTTPException) as error:
        _authenticate(token)
    logging.getLogger("httpcore.proxy").debug("outside-jwks-debug")

    assert error.value.detail == "身份验证服务暂不可用"
    assert "jwks_fetch_failed" in caplog.text and "outside-jwks-debug" in caplog.text
    assert all(value not in caplog.text for value in (token, jwks_text, url))
