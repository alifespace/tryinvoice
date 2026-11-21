import json
from io import BytesIO
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook
from psycopg import Connection

from app.api.deps import get_storage
from app.storage import StorageError
from tests.test_auth_batches_api import _body, _headers, _system_template_id

pytest_plugins = ("tests.test_auth_batches_api",)


class _Storage:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.missing: set[str] = set()
        self.fail_get: set[str] = set()
        self.fail_put = False
        self.puts: list[tuple[str, bytes, str]] = []

    def get(self, key: str) -> bytes | None:
        if key in self.fail_get:
            raise StorageError("private-key-marker")
        return None if key in self.missing else self.data.get(key)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        if self.fail_put:
            raise StorageError("private-key-marker")
        self.puts.append((key, data, content_type))

    def presign_get(self, key: str) -> tuple[str, int]:
        return f"https://storage.test/{key}?X-Amz-Signature=test-signature", 600


@pytest.fixture
def export_api(batch_api: tuple, db_conn: Connection) -> tuple:
    client, users = batch_api
    user, storage = users[0], _Storage()
    client.app.dependency_overrides[get_storage] = lambda: storage
    template = _system_template_id(client, user)
    batch = UUID(
        client.post(
            "/api/v1/batches", json=_body(template), headers=_headers(user)
        ).json()["id"]
    )
    return client, db_conn, users, user, batch, storage


def _add_transaction(
    conn: Connection,
    storage: _Storage,
    user: UUID,
    batch: UUID,
) -> tuple[UUID, UUID, str]:
    document_id, transaction_id = uuid4(), uuid4()
    key = f"{user}/{batch}/raw/{document_id}.pdf"
    conn.execute(
        "INSERT INTO public.raw_documents "
        "(id,user_id,batch_id,original_filename,mime_type,file_size_bytes,"
        "sha256_hash,storage_key) VALUES (%s,%s,%s,'receipt.pdf',"
        "'application/pdf',12,%s,%s)",
        (document_id, user, batch, uuid4().hex, key),
    )
    conn.execute(
        "INSERT INTO public.transactions "
        "(id,user_id,batch_id,category,region_scope,region_detail,tx_date,"
        "merchant,currency,original_amount,claim_amount,payment_status) "
        "VALUES (%s,%s,%s,'EAT','in_scope','美洲','2026-10-04','Cafe','USD',"
        "8.00,8.00,'paired')",
        (transaction_id, user, batch),
    )
    conn.execute(
        "INSERT INTO public.transaction_documents "
        "(transaction_id,raw_document_id,user_id,link_role) "
        "VALUES (%s,%s,%s,'receipt')",
        (transaction_id, document_id, user),
    )
    storage.data[key] = b"synthetic voucher"
    return document_id, transaction_id, key


def test_export_creates_zip_and_job_download(export_api: tuple) -> None:
    client, conn, _, user, batch, storage = export_api
    document_id, _, _ = _add_transaction(conn, storage, user, batch)

    response = client.post(f"/api/v1/batches/{batch}/export", headers=_headers(user))

    assert response.status_code == 200
    result = response.json()
    assert (result["status"], result["transactions"], result["vouchers"]) == (
        "succeeded",
        1,
        1,
    )
    assert result["expires_in"] == 600
    assert "X-Amz-Signature=" in result["download_url"]
    key, archive_bytes, content_type = storage.puts[0]
    assert key == f"{user}/{batch}/exports/{result['job_id']}.zip"
    assert content_type == "application/zip"
    with ZipFile(BytesIO(archive_bytes)) as archive:
        members = archive.namelist()
        assert ".processed_manifest.json" in members
        voucher_name = next(name for name in members if name.endswith("_receipt.pdf"))
        workbook_name = next(name for name in members if name.endswith(".xlsx"))
        manifest = json.loads(archive.read(".processed_manifest.json"))
        assert manifest["entries"][0]["original_filename"] == "receipt.pdf"
        assert manifest["entries"][0]["export_filename"] == voucher_name
        workbook = load_workbook(BytesIO(archive.read(workbook_name)), read_only=True)
        assert len(next(workbook.worksheets[0].iter_rows(values_only=True))) == 12
        workbook.close()
    assert conn.execute(
        "SELECT status,progress,result_storage_key,error_message "
        "FROM public.jobs WHERE id=%s",
        (result["job_id"],),
    ).fetchone() == ("succeeded", 100, key, None)
    assert conn.execute(
        "SELECT export_filename FROM public.transaction_documents "
        "WHERE raw_document_id=%s",
        (document_id,),
    ).fetchone() == (voucher_name,)

    job = client.get(f"/api/v1/jobs/{result['job_id']}", headers=_headers(user))
    assert job.status_code == 200
    assert job.json()["download_url"] == result["download_url"]


def test_export_writeback_only_touches_current_batch(export_api: tuple) -> None:
    client, conn, _, user, batch, storage = export_api
    document_id, current_tx, _ = _add_transaction(conn, storage, user, batch)
    other = client.post(
        "/api/v1/batches",
        json=_body(_system_template_id(client, user)),
        headers=_headers(user),
    )
    assert other.status_code == 201
    _, other_tx, _ = _add_transaction(conn, storage, user, UUID(other.json()["id"]))
    conn.execute(
        "INSERT INTO public.transaction_documents "
        "(transaction_id,raw_document_id,user_id,link_role,export_filename) "
        "VALUES (%s,%s,%s,'receipt','untouched.pdf')",
        (other_tx, document_id, user),
    )

    response = client.post(f"/api/v1/batches/{batch}/export", headers=_headers(user))

    assert response.status_code == 200
    filenames = conn.execute(
        "SELECT transaction_id,export_filename "
        "FROM public.transaction_documents WHERE raw_document_id=%s",
        (document_id,),
    ).fetchall()
    by_transaction = dict(filenames)
    assert by_transaction[current_tx] is not None
    assert by_transaction[other_tx] == "untouched.pdf"


def test_empty_export_marks_job_failed(export_api: tuple) -> None:
    client, conn, _, user, batch, _ = export_api

    response = client.post(f"/api/v1/batches/{batch}/export", headers=_headers(user))

    assert response.status_code == 409
    assert response.json()["detail"] == "批次暂无可导出交易"
    assert conn.execute(
        "SELECT status,error_message FROM public.jobs WHERE batch_id=%s",
        (batch,),
    ).fetchone() == ("failed", "导出失败。")


@pytest.mark.parametrize("failure", ["missing", "get", "put"])
def test_storage_failures_are_sanitized(export_api: tuple, failure: str) -> None:
    client, conn, _, user, batch, storage = export_api
    _, _, key = _add_transaction(conn, storage, user, batch)
    if failure == "missing":
        storage.missing.add(key)
    elif failure == "get":
        storage.fail_get.add(key)
    else:
        storage.fail_put = True

    response = client.post(f"/api/v1/batches/{batch}/export", headers=_headers(user))

    assert response.status_code == 500
    assert response.json()["detail"] == "导出失败"
    assert "private-key-marker" not in response.text and key not in response.text
    assert conn.execute(
        "SELECT status,error_message FROM public.jobs WHERE batch_id=%s",
        (batch,),
    ).fetchone() == ("failed", "导出失败。")


def test_export_and_job_are_owner_scoped_and_require_auth(
    export_api: tuple,
) -> None:
    client, conn, users, user, batch, storage = export_api
    _add_transaction(conn, storage, user, batch)
    success = client.post(f"/api/v1/batches/{batch}/export", headers=_headers(user))
    job_id = success.json()["job_id"]
    other_user = users[1]

    assert (
        client.post(
            f"/api/v1/batches/{batch}/export", headers=_headers(other_user)
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/jobs/{job_id}", headers=_headers(other_user)).status_code
        == 404
    )
    assert client.post(f"/api/v1/batches/{batch}/export").status_code == 401
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 401


def test_non_succeeded_job_has_no_download_url(export_api: tuple) -> None:
    client, conn, _, user, batch, _ = export_api
    job_id = conn.execute(
        "INSERT INTO public.jobs (user_id,batch_id,job_type,status) "
        "VALUES (%s,%s,'process_batch','running') RETURNING id",
        (user, batch),
    ).fetchone()[0]

    response = client.get(f"/api/v1/jobs/{job_id}", headers=_headers(user))

    assert response.status_code == 200
    assert response.json()["download_url"] is None
    assert response.json()["expires_in"] is None
