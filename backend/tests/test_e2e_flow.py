import hashlib
import json
import logging
import re
from decimal import Decimal
from functools import partial
from io import BytesIO
from uuid import UUID
from zipfile import ZipFile

import pymupdf
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from psycopg import Connection

from app.api.deps import get_storage
from app.storage import StorageError
from tests.test_auth_batches_api import _body, _headers, _system_template_id

pytest_plugins = ("tests.test_auth_batches_api",)
_CARD = "4242424242424242"
_EXTENSION = {"image/jpeg": "jpg", "application/pdf": "pdf"}


class _Storage:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.puts: list[tuple[str, bytes, str]] = []
        self.fail_get: set[str] = set()

    def presign_put(self, key: str) -> tuple[str, int]:
        return f"https://storage.test/{key}?X-Amz-Signature=test-signature", 600

    def head_size(self, key: str) -> int | None:
        return len(self.data[key]) if key in self.data else None

    def get(self, key: str) -> bytes | None:
        if key in self.fail_get:
            raise StorageError("secret-marker")
        return self.data.get(key)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.puts.append((key, data, content_type))
        self.data[key] = data

    def presign_get(self, key: str) -> tuple[str, int]:
        return f"https://storage.test/{key}?X-Amz-Signature=test-signature", 600

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


@pytest.fixture
def flow_api(batch_api: tuple, db_conn: Connection) -> tuple:
    client, users = batch_api
    user, storage = users[0], _Storage()
    client.app.dependency_overrides[get_storage] = lambda: storage
    headers = _headers(user)
    template = _system_template_id(client, user)
    response = client.post("/api/v1/batches", json=_body(template), headers=headers)
    assert response.status_code == 201
    return client, db_conn, user, UUID(response.json()["id"]), storage, headers


def _ocr(*lines: str) -> dict[str, object]:
    return {"lines": [{"text": line} for line in lines]}


def _pdf(text: str) -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def _upload(
    client: TestClient,
    storage: _Storage,
    user: UUID,
    batch: UUID,
    headers: dict[str, str],
    filename: str,
    content: bytes,
    *lines: str,
    mime_type: str = "image/jpeg",
    responses: list[str] | None = None,
) -> tuple[UUID, str]:
    digest = hashlib.sha256(content).hexdigest()
    path = f"/api/v1/batches/{batch}/documents"
    presign = client.post(
        f"{path}/presign",
        json={"filename": filename, "mime_type": mime_type, "sha256_hash": digest},
        headers=headers,
    )
    assert presign.status_code == 200 and not presign.json()["already_exists"]
    document_id = UUID(presign.json()["document_id"])
    key = f"{user}/{batch}/raw/{document_id}.{_EXTENSION[mime_type]}"
    storage.data[key] = content
    body: dict[str, object] = {
        "filename": filename,
        "mime_type": mime_type,
        "sha256_hash": digest,
        "document_id": str(document_id),
    }
    if lines:
        body["client_ocr_json"] = _ocr(*lines)
    confirmed = client.post(f"{path}/confirm", json=body, headers=headers)
    assert confirmed.status_code == 201 and _CARD not in confirmed.text
    if responses is not None:
        responses.append(confirmed.text)
    return document_id, key


def _items(response: dict[str, object]) -> list[dict[str, object]]:
    return [
        tx
        for g in response["groups"]
        for s in g["scopes"]
        for r in s["regions"]
        for tx in r["transactions"]
    ]


def _assert_absent(text: str, values: tuple[str, ...]) -> None:
    assert all(value not in text for value in values)


def test_full_flow(flow_api: tuple) -> None:
    client, conn, user, batch, storage, headers = flow_api
    upload = partial(_upload, client, storage, user, batch, headers)
    card = f"Card: {_CARD}"
    invoice, _ = upload(
        "uber-invoice.jpg",
        b"synthetic invoice",
        "Invoice",
        "Merchant: Uber",
        "Date: 2026-10-02",
        "Total: $20.00",
        "Tip: $3.00",
        card,
    )
    receipt_lines = (
        "Receipt",
        "Merchant: Uber",
        "Date: 2026-10-02",
        "Total: $20.00",
        card,
    )
    receipt, _ = upload("uber-receipt.jpg", b"synthetic receipt", *receipt_lines)
    reshot, _ = upload("uber-receipt-copy.jpg", b"synthetic reshot", *receipt_lines)
    upload(
        "trip-application.pdf",
        _pdf(
            "Business Trip Application\nStart: 2026-10-01\nEnd: 2026-10-03\n"
            "Destination Airport: LAX"
        ),
        mime_type="application/pdf",
    )
    path = f"/api/v1/batches/{batch}/process"
    processed = client.post(path, headers=headers)
    assert processed.status_code == 200 and _CARD not in processed.text
    first = processed.json()
    assert (first["processed_documents"], first["transactions"]) == (4, 1)
    assert first["missing_alerts"] >= 1
    assert (
        conn.execute(
            "SELECT count(*) FROM public.raw_documents WHERE id IN (%s,%s) "
            "AND duplicate_of_id IS NOT NULL",
            (receipt, reshot),
        ).fetchone()[0]
        == 1
    )
    assert _CARD not in str(
        conn.execute(
            "SELECT extracted_json::text FROM public.raw_documents WHERE batch_id=%s",
            (batch,),
        ).fetchall()
    )
    listing = client.get(f"/api/v1/batches/{batch}/transactions", headers=headers)
    assert listing.status_code == 200 and _CARD not in listing.text
    transactions = _items(listing.json())
    assert len(transactions) == 1
    assert Decimal(transactions[0]["claim_amount"]) == Decimal("17.00")
    assert "TIP_EXCLUDED" in transactions[0]["flags"]
    transaction_id = transactions[0]["id"]
    assert (
        conn.execute(
            "SELECT count(*) FROM public.transaction_documents "
            "WHERE transaction_id=%s AND raw_document_id IN (%s,%s)",
            (transaction_id, invoice, receipt),
        ).fetchone()[0]
        == 2
    )
    first_ids = [str(tx["id"]) for tx in transactions]
    replay = client.post(path, headers=headers)
    assert (
        replay.status_code == 200
        and replay.json()["processed_documents"] == 0
        and _CARD not in replay.text
    )
    replay_listing = client.get(
        f"/api/v1/batches/{batch}/transactions", headers=headers
    )
    assert [str(tx["id"]) for tx in _items(replay_listing.json())] == first_ids
    assert _CARD not in replay_listing.text
    patched = client.patch(
        f"/api/v1/transactions/{transaction_id}",
        json={"tip_amount": "4.00", "notes": "Reviewed"},
        headers=headers,
    )
    assert (
        patched.status_code == 200
        and patched.json()["is_user_verified"] is True
        and _CARD not in patched.text
    )
    assert Decimal(patched.json()["claim_amount"]) == Decimal("16.00")
    rerun = client.post(path, headers=headers)
    assert (
        rerun.status_code == 200
        and rerun.json()["processed_documents"] == 0
        and _CARD not in rerun.text
    )
    final_listing = client.get(f"/api/v1/batches/{batch}/transactions", headers=headers)
    tx = _items(final_listing.json())
    assert (
        len(tx) == 1
        and tx[0]["notes"] == "Reviewed"
        and _CARD not in final_listing.text
    )
    assert Decimal(tx[0]["claim_amount"]) == Decimal("16.00")
    exported = client.post(f"/api/v1/batches/{batch}/export", headers=headers)
    assert exported.status_code == 200 and _CARD not in exported.text
    result = exported.json()
    _, archive_bytes, content_type = storage.puts[-1]
    assert content_type == "application/zip"
    assert result["transactions"] == 1 and result["vouchers"] == 2
    with ZipFile(BytesIO(archive_bytes)) as archive:
        members = archive.namelist()
        assert ".processed_manifest.json" in members
        workbook_names = [name for name in members if name.endswith(".xlsx")]
        voucher_names = [
            name for name in members if name.endswith(("_invoice.jpg", "_receipt.jpg"))
        ]
        assert (
            len(workbook_names) == 1 and len(voucher_names) == 2 and len(members) == 4
        )
        assert all(
            re.fullmatch(r"Others_20261002_USD_20-00_(invoice|receipt)\.jpg", name)
            for name in voucher_names
        )
        manifest_text = archive.read(".processed_manifest.json").decode()
        assert _CARD not in manifest_text and all(_CARD not in name for name in members)
        manifest = json.loads(manifest_text)
        entries = manifest["entries"]
        assert len(entries) == 2
        assert {
            (item["original_filename"], item["export_filename"]) for item in entries
        } == {
            ("uber-invoice.jpg", next(n for n in voucher_names if "_invoice." in n)),
            ("uber-receipt.jpg", next(n for n in voucher_names if "_receipt." in n)),
        }
        workbook = load_workbook(
            BytesIO(archive.read(workbook_names[0])), read_only=True, data_only=False
        )
        assert _CARD not in str([c.value for s in workbook for r in s for c in r])
        rows = list(workbook.worksheets[0].iter_rows(values_only=True))
        assert len(rows[0]) == 12
        details = [row for row in rows if row[6] == "Uber"]
        assert len(details) == 1
        assert details[0][9] == Decimal("16.00") and details[0][11] == "Reviewed"
        workbook.close()
    job_response = client.get(f"/api/v1/jobs/{result['job_id']}", headers=headers)
    job = job_response.json()
    assert (
        job["status"] == "succeeded"
        and job["download_url"] == result["download_url"]
        and _CARD not in job_response.text
    )


def test_no_sensitive_values_in_logs(batch_api, db_conn, caplog, capfd) -> None:
    caplog.set_level(logging.DEBUG)
    client, users = batch_api
    user, storage = users[0], _Storage()
    client.app.dependency_overrides[get_storage] = lambda: storage
    headers = _headers(user)
    token = headers["Authorization"].removeprefix("Bearer ")
    template = _system_template_id(client, user)
    batch_response = client.post(
        "/api/v1/batches", json=_body(template), headers=headers
    )
    assert batch_response.status_code == 201
    batch = UUID(batch_response.json()["id"])
    responses = [batch_response.text]
    upload = partial(
        _upload, client, storage, user, batch, headers, responses=responses
    )
    _, image_key = upload(
        "card-receipt.jpg",
        b"synthetic logged receipt",
        "Receipt",
        "Merchant: Uber",
        "Date: 2026-10-02",
        "Total: $20.00",
        f"Card: {_CARD}",
    )
    _, pdf_key = upload(
        "trip-application.pdf",
        _pdf("Business Trip Application\nDestination Airport: LAX"),
        mime_type="application/pdf",
    )
    storage.fail_get.add(pdf_key)
    process = client.post(f"/api/v1/batches/{batch}/process", headers=headers)
    assert process.status_code == 200 and process.json()["deferred_documents"] >= 1
    pending = db_conn.execute(
        "SELECT ocr_status FROM public.raw_documents WHERE storage_key=%s",
        (pdf_key,),
    ).fetchone()[0]
    assert pending == "pending"
    storage.fail_get.clear()
    storage.fail_get.add(image_key)
    failed_export = client.post(f"/api/v1/batches/{batch}/export", headers=headers)
    assert failed_export.status_code == 500
    assert failed_export.json()["detail"] == "导出失败"
    storage.fail_get.clear()
    exported = client.post(f"/api/v1/batches/{batch}/export", headers=headers)
    job_response = client.get(
        f"/api/v1/jobs/{exported.json()['job_id']}", headers=headers
    )
    assert exported.status_code == job_response.status_code == 200
    responses += [r.text for r in (process, failed_export, exported, job_response)]
    captured = capfd.readouterr()
    log_output = caplog.text + captured.out + captured.err
    _assert_absent(
        log_output + "".join(responses),
        (_CARD, "secret-marker", image_key, pdf_key, token, "Authorization", "Bearer"),
    )
    _assert_absent(log_output, ("X-Amz-Signature", "test-signature"))
