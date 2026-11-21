import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from app.config import Settings

_logger = logging.getLogger(__name__)
_MIN_REFRESH_INTERVAL_SECONDS = 30.0
_suppress_http_logs: ContextVar[bool] = ContextVar(
    "suppress_jwks_http_logs", default=False
)


class _JwksLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _suppress_http_logs.get()


_jwks_log_filter = _JwksLogFilter()
for _http_logger_name in (
    "httpx",
    "httpcore",
    "httpcore.http11",
    "httpcore.connection",
    "httpcore.proxy",
    "httpcore.socks",
):
    logging.getLogger(_http_logger_name).addFilter(_jwks_log_filter)


class JwksUnavailable(Exception):
    """JWKS is unavailable and no sufficiently recent cache can be used."""


class UnknownSigningKey(Exception):
    """The JWKS does not contain a usable key for the requested key ID."""


@dataclass(frozen=True)
class _JwksCache:
    keys: list[dict[str, Any]]
    fetched_at: float


_cache: _JwksCache | None = None
_last_forced_refresh: float | None = None


def reset_jwks_cache() -> None:
    global _cache, _last_forced_refresh
    _cache = None
    _last_forced_refresh = None


def _jwks_url(settings: Settings) -> str | None:
    if settings.supabase_jwks_url:
        return settings.supabase_jwks_url
    if settings.supabase_url:
        return f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    return None


def _fetch_jwks(url: str, timeout: float) -> list[dict[str, Any]]:
    log_token = _suppress_http_logs.set(True)
    try:
        response = httpx.get(url, timeout=timeout)
        if response.status_code != 200:
            raise httpx.HTTPStatusError(
                "JWKS endpoint did not return HTTP 200",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
            raise ValueError("invalid JWKS")
        return [key for key in data["keys"] if isinstance(key, dict)]
    finally:
        _suppress_http_logs.reset(log_token)


def _cached_keys(
    now: float, ttl: int, *, force_refresh: bool
) -> list[dict[str, Any]] | None:
    if _cache is None:
        return None
    age = now - _cache.fetched_at
    if not force_refresh and age < ttl:
        return _cache.keys
    if (
        force_refresh
        and _last_forced_refresh is not None
        and now - _last_forced_refresh < _MIN_REFRESH_INTERVAL_SECONDS
    ):
        if age <= ttl * 2:
            return _cache.keys
        raise JwksUnavailable
    return None


def _stale_keys_or_raise(ttl: int) -> list[dict[str, Any]]:
    _logger.warning("jwks_fetch_failed")
    if _cache is not None:
        if time.monotonic() - _cache.fetched_at <= ttl * 2:
            return _cache.keys
    raise JwksUnavailable


def get_jwks(
    settings: Settings, *, force_refresh: bool = False
) -> list[dict[str, Any]]:
    global _cache, _last_forced_refresh
    now = time.monotonic()
    ttl = settings.supabase_jwks_cache_seconds
    cached = _cached_keys(now, ttl, force_refresh=force_refresh)
    if cached is not None:
        return cached

    url = _jwks_url(settings)
    if url is None:
        raise JwksUnavailable
    if force_refresh:
        _last_forced_refresh = now
    try:
        keys = _fetch_jwks(url, settings.supabase_jwks_timeout_seconds)
    except httpx.HTTPError, ValueError:
        return _stale_keys_or_raise(ttl)

    _cache = _JwksCache(keys=keys, fetched_at=now)
    return keys


def _find_signing_key(keys: list[dict[str, Any]], key_id: str) -> jwt.PyJWK | None:
    for key_data in keys:
        if key_data.get("kid") != key_id:
            continue
        try:
            key = jwt.PyJWK.from_dict(key_data)
        except jwt.PyJWTError, KeyError, ValueError, TypeError:
            continue
        if key.algorithm_name == "ES256":
            return key
    return None


def get_signing_key(settings: Settings, key_id: str) -> jwt.PyJWK:
    key = _find_signing_key(get_jwks(settings), key_id)
    if key is not None:
        return key
    key = _find_signing_key(get_jwks(settings, force_refresh=True), key_id)
    if key is None:
        raise UnknownSigningKey
    return key
