import hmac
from collections.abc import Mapping
from datetime import UTC, datetime
from email.message import Message
from hashlib import sha256
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.config import Settings


class StorageNotConfigured(Exception):
    pass


class StorageError(Exception):
    pass


class ObjectStorage(Protocol):
    def presign_put(self, key: str) -> tuple[str, int]: ...

    def head_size(self, key: str) -> int | None: ...

    def delete(self, key: str) -> None: ...


def _encoded_query(params: Mapping[str, str]) -> str:
    return urlencode(sorted(params.items()), quote_via=quote, safe="~")


def sigv4_query_signature(
    method: str,
    host: str,
    canonical_uri: str,
    query_params: Mapping[str, str],
    region: str,
    service: str,
    secret: str,
    amz_date: str,
) -> str:
    date = amz_date[:8]
    scope = f"{date}/{region}/{service}/aws4_request"
    canonical = "\n".join(
        (
            method.upper(),
            canonical_uri,
            _encoded_query(query_params),
            f"host:{host}\n",
            "host",
            "UNSIGNED-PAYLOAD",
        )
    )
    to_sign = "\n".join(
        (
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            sha256(canonical.encode()).hexdigest(),
        )
    )

    signing_key = ("AWS4" + secret).encode()
    for value in (date, region, service, "aws4_request"):
        signing_key = hmac.new(signing_key, value.encode(), sha256).digest()
    return hmac.new(signing_key, to_sign.encode(), sha256).hexdigest()


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _configuration(self) -> tuple[str, str, str, str]:
        settings = self._settings
        endpoint, bucket = settings.storage_endpoint_url, settings.storage_bucket
        access, secret = (
            settings.storage_access_key_id,
            settings.storage_secret_access_key,
        )
        if not (
            endpoint
            and bucket
            and access
            and access.get_secret_value()
            and secret
            and secret.get_secret_value()
        ):
            raise StorageNotConfigured
        return (
            endpoint,
            bucket,
            access.get_secret_value(),
            secret.get_secret_value(),
        )

    def _url(self, method: str, key: str) -> str:
        endpoint, bucket, access_key, secret = self._configuration()
        try:
            parts = urlsplit(endpoint)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise ValueError
            host = parts.netloc
            path = parts.path.rstrip("/")
            encoded_key = "/".join(quote(part, safe="~") for part in key.split("/"))
            uri = f"{path}/{quote(bucket, safe='~')}/{encoded_key}"
            now = datetime.now(UTC)
            amz_date = now.strftime("%Y%m%dT%H%M%SZ")
            date = amz_date[:8]
            scope = f"{date}/{self._settings.storage_region}/s3/aws4_request"
            params = {
                "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
                "X-Amz-Credential": f"{access_key}/{scope}",
                "X-Amz-Date": amz_date,
                "X-Amz-Expires": str(self._settings.storage_presign_ttl_seconds),
                "X-Amz-SignedHeaders": "host",
            }
            params["X-Amz-Signature"] = sigv4_query_signature(
                method,
                host,
                uri or "/",
                params,
                self._settings.storage_region,
                "s3",
                secret,
                amz_date,
            )
            return urlunsplit((parts.scheme, host, uri, _encoded_query(params), ""))
        except TypeError, ValueError:
            raise StorageError from None

    def presign_put(self, key: str) -> tuple[str, int]:
        return self._url("PUT", key), self._settings.storage_presign_ttl_seconds

    def _request(self, method: str, key: str) -> tuple[int, Message[str, str]]:
        url = self._url(method, key)
        try:
            with urlopen(Request(url, method=method), timeout=10) as response:
                return response.status, response.headers
        except HTTPError as error:
            return error.code, error.headers
        except OSError, ValueError:
            raise StorageError from None

    def head_size(self, key: str) -> int | None:
        status, headers = self._request("HEAD", key)
        if status == 404:
            return None
        if status != 200:
            raise StorageError
        try:
            size = headers.get("Content-Length")
            if size is None:
                raise ValueError
            return int(size)
        except AttributeError, ValueError:
            raise StorageError from None

    def delete(self, key: str) -> None:
        status, _ = self._request("DELETE", key)
        if status not in {200, 204, 404}:
            raise StorageError
