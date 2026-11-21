import logging
from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

import jwt
import psycopg
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg import Connection

from app.auth_jwks import JwksUnavailable, UnknownSigningKey, get_signing_key
from app.config import Settings, get_settings
from app.extraction.models import DocumentExtractorProtocol
from app.extraction.tier1 import DeterministicExtractor
from app.storage import ObjectStorage, S3Storage

_bearer = HTTPBearer(auto_error=False)
_logger = logging.getLogger(__name__)


def _issuer(settings: Settings) -> str | None:
    if settings.supabase_jwt_issuer:
        return settings.supabase_jwt_issuer
    if settings.supabase_url:
        return f"{settings.supabase_url.rstrip('/')}/auth/v1"
    return None


def _invalid_token() -> HTTPException:
    _logger.warning("token_invalid")
    return HTTPException(
        status_code=401,
        detail="无效的凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_payload(token: str, settings: Settings) -> dict[str, object]:
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        signing_key: str | jwt.PyJWK
        if algorithm == "ES256":
            key_id = header.get("kid")
            if not isinstance(key_id, str) or not key_id:
                raise ValueError
            signing_key = get_signing_key(settings, key_id)
        elif (
            algorithm == "HS256"
            and settings.supabase_allow_legacy_hs256
            and settings.supabase_jwt_secret is not None
        ):
            signing_key = settings.supabase_jwt_secret.get_secret_value()
        else:
            raise ValueError
        issuer = _issuer(settings)
        if issuer is None:
            raise JwksUnavailable
        return jwt.decode(
            token,
            signing_key,
            algorithms=[algorithm],
            audience=settings.supabase_jwt_audience,
            issuer=issuer,
            options={"require": ["exp", "sub", "aud", "iss"], "strict_aud": True},
        )
    except JwksUnavailable:
        raise
    except jwt.PyJWTError, UnknownSigningKey, TypeError, ValueError:
        raise _invalid_token() from None


def _user_id_from_payload(payload: dict[str, object]) -> UUID:
    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise _invalid_token()
    try:
        return UUID(subject)
    except ValueError:
        raise _invalid_token() from None


def _user_id_without_credentials(request: Request, settings: Settings) -> UUID:
    authorization = request.headers.get("authorization", "")
    parts = authorization.split(maxsplit=1)
    if parts and parts[0].casefold() == "bearer":
        raise _invalid_token()
    if settings.app_env == "local" and settings.local_dev_user_id is not None:
        return settings.local_dev_user_id
    raise HTTPException(
        status_code=401,
        detail="未提供凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> UUID:
    settings = get_settings()
    if credentials is None:
        return _user_id_without_credentials(request, settings)
    try:
        payload = _decode_payload(credentials.credentials, settings)
        return _user_id_from_payload(payload)
    except JwksUnavailable:
        raise HTTPException(status_code=503, detail="身份验证服务暂不可用") from None


def get_conn() -> Iterator[Connection]:
    database_url = get_settings().database_url
    if database_url is None:
        raise HTTPException(status_code=503, detail="数据库未配置")

    with psycopg.connect(database_url.get_secret_value(), autocommit=True) as conn:
        yield conn


def get_storage() -> ObjectStorage:
    return S3Storage(get_settings())


def get_extractor() -> DocumentExtractorProtocol:
    return DeterministicExtractor()
