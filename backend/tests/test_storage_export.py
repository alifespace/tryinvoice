from typing import Any
from urllib.error import HTTPError
from uuid import UUID

import pytest
from pydantic import SecretStr

import app.storage as storage_module
from app.config import Settings
from app.storage import (
    S3Storage,
    StorageError,
    StorageNotConfigured,
    export_storage_key,
)


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _storage() -> S3Storage:
    return S3Storage(
        Settings(
            _env_file=None,
            storage_endpoint_url="https://storage.invalid",
            storage_bucket="exports",
            storage_access_key_id=SecretStr("example-access"),
            storage_secret_access_key=SecretStr("private-secret"),
            storage_presign_ttl_seconds=321,
        )
    )


def test_put_passes_body_content_type_and_safe_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: int) -> _Response:
        captured.update(
            url=request.full_url,
            data=request.data,
            content_type=request.get_header("Content-type"),
            method=request.get_method(),
            timeout=timeout,
        )
        return _Response(204)

    monkeypatch.setattr(storage_module, "urlopen", fake_urlopen)
    _storage().put("u/b/exports/j.zip", b"zip-data", "application/zip")
    assert captured["method"] == "PUT" and captured["data"] == b"zip-data"
    assert captured["content_type"] == "application/zip" and captured["timeout"] == 10
    assert "private-secret" not in captured["url"]

    monkeypatch.setattr(
        storage_module, "urlopen", lambda *args, **kwargs: _Response(500)
    )
    with pytest.raises(StorageError):
        _storage().put("key", b"x", "application/zip")

    monkeypatch.setattr(
        storage_module,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HTTPError("https://private-secret.invalid", 403, "forbidden", {}, None)
        ),
    )
    with pytest.raises(StorageError) as error:
        _storage().put("key", b"x", "application/zip")
    assert "private-secret" not in str(error.value)

    monkeypatch.setattr(
        storage_module,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("private-secret")),
    )
    with pytest.raises(StorageError) as error:
        _storage().put("key", b"x", "application/zip")
    assert "private-secret" not in str(error.value)


def test_presign_get_and_export_key() -> None:
    url, ttl = _storage().presign_get("u/b/exports/j.zip")
    assert "X-Amz-Signature=" in url and ttl == 321
    assert (
        export_storage_key(UUID(int=1), UUID(int=2), UUID(int=3))
        == f"{UUID(int=1)}/{UUID(int=2)}/exports/{UUID(int=3)}.zip"
    )


def test_put_requires_storage_configuration() -> None:
    storage = S3Storage(Settings(_env_file=None))
    with pytest.raises(StorageNotConfigured):
        storage.put("key", b"x", "application/zip")
