from collections.abc import Iterator
from functools import partial
from typing import Any
from urllib.error import HTTPError, URLError
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg import Connection
from pydantic import SecretStr

from app.api.deps import get_current_user_id, get_storage
from app.config import Settings
from app.storage import S3Storage, StorageError, sigv4_query_signature
from tests.test_auth_batches_api import _body, _headers, _system_template_id

pytest_plugins = ("tests.test_auth_batches_api",)
_HASH = "a" * 64


def _s3() -> S3Storage:
    return S3Storage(
        Settings(
            _env_file=None,
            storage_endpoint_url="https://storage.invalid/base",
            storage_bucket="my bucket",
            storage_access_key_id=SecretStr("example-access"),
            storage_secret_access_key=SecretStr("not-a-real-secret"),
        )
    )


class FakeStorage:
    def __init__(self) -> None:
        self.puts: list[str] = []
        self.heads: list[str] = []
        self.deletes: list[str] = []
        self.size: int | None = 12345
        self.fail_delete = False

    def presign_put(self, key: str) -> tuple[str, int]:
        self.puts.append(key)
        return "https://upload.invalid/signed", 900

    def head_size(self, key: str) -> int | None:
        self.heads.append(key)
        return self.size

    def delete(self, key: str) -> None:
        self.deletes.append(key)
        if self.fail_delete:
            raise StorageError


@pytest.fixture
def api(
    batch_api: tuple[TestClient, list[UUID]], db_conn: Connection
) -> Iterator[tuple]:
    client, users = batch_api
    user, storage = users[0], FakeStorage()
    client.headers.update(_headers(user))
    client.app.dependency_overrides[get_storage] = lambda: storage
    template = _system_template_id(client, user)
    batch = UUID(
        client.post(
            "/api/v1/batches", json=_body(template), headers=_headers(user)
        ).json()["id"]
    )
    yield client, db_conn, user, storage, batch


def post(
    client: TestClient, batch: UUID, action: str, digest: str = _HASH, **extra: Any
) -> Any:
    body = {
        "filename": "receipt.jpg",
        "mime_type": "image/jpeg",
        "sha256_hash": digest,
        **extra,
    }
    return client.post(f"/api/v1/batches/{batch}/documents/{action}", json=body)


def test_sigv4_vector_and_path_style_encoding() -> None:
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": "AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request",
        "X-Amz-Date": "20130524T000000Z",
        "X-Amz-Expires": "86400",
        "X-Amz-SignedHeaders": "host",
    }
    args = (
        "GET",
        "examplebucket.s3.amazonaws.com",
        "/test.txt",
        params,
        "us-east-1",
        "s3",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "20130524T000000Z",
    )
    assert sigv4_query_signature(*args) == (
        "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"
    )
    url, ttl = _s3().presign_put("user id/batch/raw/file name.jpg")
    assert (
        ttl == 900 and "/base/my%20bucket/user%20id/batch/raw/file%20name.jpg?" in url
    )
    assert "X-Amz-Expires=900" in url and "not-a-real-secret" not in url


def test_presign_validation_and_tenant_scope(api: tuple) -> None:
    client, conn, user, storage, batch = api
    call = partial(post, client, batch)
    response = call("presign")
    body = response.json()
    assert response.status_code == 200 and body["already_exists"] is False
    assert body["upload_method"] == "PUT" and body["expires_in"] == 900
    assert storage.puts == [f"{user}/{batch}/raw/{body['document_id']}.jpg"]
    assert conn.execute("SELECT count(*) FROM public.raw_documents").fetchone()[0] == 0
    assert call("presign", "ABC").status_code == 422
    assert call("presign", mime_type="text/plain").status_code == 422
    client.app.dependency_overrides[get_current_user_id] = lambda: UUID(int=0)
    assert call("presign").status_code == 404


def test_confirm_metadata_idempotency_and_missing_upload(api: tuple) -> None:
    client, conn, user, storage, batch = api
    call = partial(post, client, batch)
    presigned = call("presign").json()
    document = presigned["document_id"]
    client.app.dependency_overrides[get_current_user_id] = lambda: UUID(int=0)
    assert call("confirm", document_id=document).status_code == 404
    client.app.dependency_overrides.pop(get_current_user_id)
    storage.size = None
    assert call("confirm", document_id=document).status_code == 409
    storage.size = 12345
    extra = {
        "document_id": document,
        "client_ocr_json": {"lines": ["sample"]},
        "exif_taken_at": "2026-11-21T12:00:00Z",
    }
    created = call("confirm", **extra)
    assert created.status_code == 201 and created.json()["file_size_bytes"] == 12345
    assert "user_id" not in created.json() and "storage_key" not in created.json()
    row = conn.execute(
        "SELECT user_id, storage_key, client_ocr_json, exif_taken_at "
        "FROM public.raw_documents"
    ).fetchone()
    assert row[:2] == (user, f"{user}/{batch}/raw/{document}.jpg")
    assert row[2] == {"lines": ["sample"]} and row[3] is not None
    assert call("confirm", **extra).status_code == 200
    assert (
        len(storage.heads) == 2
        and conn.execute("SELECT count(*) FROM public.raw_documents").fetchone()[0] == 1
    )
    assert call("presign").json()["already_exists"]


def test_batch_delete_removes_objects_before_database(api: tuple) -> None:
    client, conn, user, storage, batch = api
    call = partial(post, client, batch)
    for digest in (_HASH, "b" * 64):
        document = call("presign", digest).json()["document_id"]
        assert call("confirm", digest, document_id=document).status_code == 201
    conn.execute(
        "INSERT INTO public.raw_documents "
        "(user_id, batch_id, original_filename, mime_type, file_size_bytes, "
        "sha256_hash, storage_key) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (user, batch, "legacy.jpg", "image/jpeg", 1, "c" * 64, "legacy-key"),
    )
    keys = {
        row[0]
        for row in conn.execute(
            "SELECT storage_key FROM public.raw_documents WHERE batch_id=%s", (batch,)
        ).fetchall()
    }
    assert client.delete(f"/api/v1/batches/{batch}").status_code == 204
    assert set(storage.deletes) == {
        key for key in keys if key.startswith(f"{user}/{batch}/raw/")
    }
    assert (
        conn.execute(
            "SELECT count(*) FROM public.raw_documents WHERE batch_id=%s", (batch,)
        ).fetchone()[0]
        == 0
    )
    storage.fail_delete = True
    template = _system_template_id(client, api[2])
    other = UUID(
        client.post(
            "/api/v1/batches",
            json=_body(template),
            headers=_headers(api[2]),
        ).json()["id"]
    )
    digest = "c" * 64
    other_call = partial(post, client, other)
    doc = other_call("presign", digest).json()["document_id"]
    assert other_call("confirm", digest, document_id=doc).status_code == 201
    assert client.delete(f"/api/v1/batches/{other}").status_code == 502
    assert (
        conn.execute(
            "SELECT count(*) FROM public.reimbursement_batches WHERE id=%s", (other,)
        ).fetchone()[0]
        == 1
    )


def test_unconfigured_storage_is_503_and_s3_http_errors_are_safe(
    api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.storage as module

    client, _, user, _, batch = api
    call = partial(post, client, batch)
    client.app.dependency_overrides.pop(get_storage)
    client.headers.pop("Authorization")
    monkeypatch.setattr(
        "app.api.deps.get_settings",
        lambda: Settings(_env_file=None, app_env="local", local_dev_user_id=user),
    )
    assert call("presign").status_code == 503

    storage = _s3()
    monkeypatch.setattr(
        module,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(HTTPError("x", 404, "", {}, None)),
    )
    assert storage.head_size("k") is None and storage.delete("k") is None
    monkeypatch.setattr(
        module, "urlopen", lambda *a, **k: (_ for _ in ()).throw(URLError("hidden"))
    )
    with pytest.raises(StorageError):
        storage.head_size("k")
