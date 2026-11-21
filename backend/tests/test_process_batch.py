from datetime import date
from decimal import Decimal
from urllib.error import HTTPError
from uuid import UUID, uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient
from psycopg import Connection
from psycopg.types.json import Jsonb

from app.api.deps import get_extractor, get_storage
from app.batches.sync import sync_transactions, update_batch_summary
from app.extraction.models import ExtractedDocument, ExtractionInput
from app.extraction.tier1 import DeterministicExtractor
from app.rules.documents import PartitionResult
from app.rules.transactions import TransactionBuildResult, TransactionDraft
from app.storage import S3Storage, StorageError
from tests.test_auth_batches_api import _body, _headers, _system_template_id

pytest_plugins = ("tests.test_auth_batches_api",)
_CARD = "4242424242424242"


class _Storage:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.fail: set[str] = set()
        self.reads: list[str] = []

    def get(self, key: str) -> bytes | None:
        self.reads.append(key)
        if key in self.fail:
            raise StorageError
        return self.data.get(key)


class _Extractor:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = ""
        self.delegate = DeterministicExtractor()

    def extract(self, source: ExtractionInput) -> ExtractedDocument | None:
        self.calls += 1
        if self.fail == "raise":
            raise RuntimeError("synthetic failure")
        return None if self.fail == "none" else self.delegate.extract(source)


@pytest.fixture
def process_api(batch_api, db_conn: Connection) -> tuple:
    client, users = batch_api
    user, storage, extractor = users[0], _Storage(), _Extractor()
    client.headers.update(_headers(user))
    client.app.dependency_overrides.update(
        {get_storage: lambda: storage, get_extractor: lambda: extractor}
    )
    template = _system_template_id(client, user)
    batch = UUID(
        client.post(
            "/api/v1/batches", json=_body(template), headers=_headers(user)
        ).json()["id"]
    )
    return client, db_conn, users, user, batch, storage, extractor


def _ocr(*lines: str) -> dict[str, object]:
    return {"lines": [{"text": line} for line in lines]}


def _add(
    conn: Connection,
    user: UUID,
    batch: UUID,
    mime: str,
    ocr: dict[str, object] | None = None,
) -> tuple[UUID, str]:
    doc, key = uuid4(), f"{user}/{batch}/raw/{uuid4()}"
    conn.execute(
        "INSERT INTO public.raw_documents "
        "(id,user_id,batch_id,original_filename,mime_type,file_size_bytes,"
        "sha256_hash,storage_key,client_ocr_json) VALUES (%s,%s,%s,'x',%s,10,%s,%s,%s)",
        (doc, user, batch, mime, uuid4().hex, key, Jsonb(ocr) if ocr else None),
    )
    return doc, key


def _draft(
    document_id: UUID,
    merchant: str,
    invoice: bool = False,
    flags: tuple[str, ...] = (),
) -> TransactionDraft:
    document = str(document_id)
    return TransactionDraft(
        *(
            "EAT",
            "in_scope",
            "美洲",
            date(2026, 10, 4),
            merchant,
            "USD",
            Decimal("8.00"),
            Decimal("0.00"),
            1,
            Decimal("8.00"),
            None,
            None,
            "paired",
            flags,
            document if invoice else None,
            None if invoice else document,
        )
    )


def _pdf(text: str) -> bytes:
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), text)
    data = pdf.tobytes()
    pdf.close()
    return data


def test_pipeline_incremental_verified_and_batch_summary(process_api: tuple) -> None:
    client, conn, _, user, batch, storage, extractor = process_api
    invoice, _ = _add(
        conn,
        user,
        batch,
        "image/jpeg",
        _ocr(
            "Invoice",
            "Merchant: Uber",
            "Date: 2026-10-02",
            "Total: $20.00",
            "Tip: 3.00",
            f"Card: {_CARD}",
        ),
    )
    receipt, _ = _add(
        conn,
        user,
        batch,
        "image/jpeg",
        _ocr(
            "Receipt",
            "Merchant: Uber",
            "Date: 2026-10-02",
            "Total: $20.00",
            f"Card: {_CARD}",
        ),
    )
    _, key = _add(conn, user, batch, "application/pdf")
    storage.data[key] = _pdf(
        "Business Trip Application\nStart: 2026-10-01\nEnd: 2026-10-03\n"
        "Destination Airport: LAX"
    )
    path = f"/api/v1/batches/{batch}/process"
    first = client.post(path).json()
    assert (
        first["processed_documents"],
        first["transactions"],
        first["missing_alerts"],
    ) == (3, 1, 2)
    tx = conn.execute("SELECT id,claim_amount FROM public.transactions").fetchone()
    assert tx[1] == Decimal("17.00") and extractor.calls == 3
    assert storage.reads == [key]
    summary = conn.execute(
        "SELECT trip_start_date,trip_end_date,destinations,status "
        "FROM public.reimbursement_batches WHERE id=%s",
        (batch,),
    ).fetchone()
    assert summary == (date(2026, 10, 1), date(2026, 10, 3), ["LAX"], "review_required")
    values = conn.execute(
        "SELECT extracted_json::text FROM public.raw_documents WHERE id IN (%s,%s)",
        (invoice, receipt),
    ).fetchall()
    if any(_CARD in row[0] for row in values):
        pytest.fail("敏感值不应进入提取结果")
    replay = client.post(path).json()
    assert replay["processed_documents"] == 0 and extractor.calls == 3
    assert conn.execute("SELECT id FROM public.transactions").fetchone()[0] == tx[0]

    conn.execute(
        "UPDATE public.transactions SET is_user_verified=true,notes='保留',"
        "claim_amount=88.88 WHERE id=%s",
        (tx[0],),
    )
    incremental_doc, _ = _add(
        conn,
        user,
        batch,
        "image/jpeg",
        _ocr("Receipt", "Merchant: Cafe", "Date: 2026-10-04", "Total: $8.00"),
    )
    stale = conn.execute(
        "INSERT INTO public.transactions "
        "(user_id,batch_id,category,region_scope,region_detail,tx_date,merchant,"
        "currency,original_amount,claim_amount,payment_status) "
        "VALUES (%s,%s,'UNKNOWN','out_of_scope','其他','2026-10-01','stale',"
        "'USD',1,1,'missing_invoice') RETURNING id",
        (user, batch),
    ).fetchone()[0]
    last = client.post(path).json()
    assert last["processed_documents"] == 1 and last["transactions"] == 2
    assert extractor.calls == 4
    assert conn.execute(
        "SELECT is_user_verified,notes,claim_amount FROM public.transactions WHERE id=%s",  # noqa: E501
        (tx[0],),
    ).fetchone() == (True, "保留", Decimal("88.88"))
    assert (
        conn.execute(
            "SELECT count(*) FROM public.transactions WHERE id=%s", (stale,)
        ).fetchone()[0]
        == 0
    )
    assert conn.execute(
        "SELECT status,progress FROM public.jobs WHERE id=%s", (last["job_id"],)
    ).fetchone() == ("succeeded", 100)
    current = conn.execute(
        "SELECT transaction_id FROM public.transaction_documents "
        "WHERE raw_document_id=%s",
        (incremental_doc,),
    ).fetchone()[0]
    new_doc, _ = _add(conn, user, batch, "image/jpeg")
    conn.execute("UPDATE public.transactions SET notes='keep' WHERE id=%s", (current,))
    draft = _draft(incremental_doc, "Updated", True, ("MEAL_SPLIT",))
    inserted = _draft(new_doc, "New", flags=("MEAL_SPLIT",))
    protected = _draft(invoice, "Changed", True)
    sync_transactions(conn, user, batch, (draft, inserted, protected))
    assert conn.execute(
        "SELECT merchant,notes FROM public.transactions WHERE id=%s", (current,)
    ).fetchone() == ("Updated", "keep")
    assert conn.execute(
        "SELECT merchant,notes,claim_amount FROM public.transactions WHERE id=%s",
        (tx[0],),
    ).fetchone() == ("Uber", "保留", Decimal("88.88"))
    links = conn.execute(
        """SELECT raw_document_id,transaction_id,link_role FROM
        public.transaction_documents WHERE raw_document_id IN (%s,%s)""",
        (invoice, receipt),
    ).fetchall()
    assert set(links) == {(invoice, tx[0], "invoice"), (receipt, tx[0], "receipt")}
    assert (
        conn.execute(
            "SELECT link_role FROM public.transaction_documents "
            "WHERE raw_document_id=%s",
            (incremental_doc,),
        ).fetchone()[0]
        == "invoice"
    )
    inserted_id, owner, role = conn.execute(
        "SELECT transaction_id,user_id,link_role FROM public.transaction_documents "
        "WHERE raw_document_id=%s",
        (new_doc,),
    ).fetchone()
    assert (owner, role) == (user, "receipt")
    assert conn.execute(
        "SELECT flags FROM public.transactions WHERE id=%s", (inserted_id,)
    ).fetchone()[0] == ["MEAL_SPLIT"]
    sync_transactions(conn, user, batch, ())
    assert (
        conn.execute(
            "SELECT count(*) FROM public.transactions WHERE id=%s", (current,)
        ).fetchone()[0]
        == 0
    )
    partition = PartitionResult((), (), (), (), (), {str(receipt): str(invoice)})
    conn.execute(
        "UPDATE public.raw_documents SET duplicate_of_id=%s WHERE id=%s",
        (invoice, new_doc),
    )
    update_batch_summary(conn, batch, partition, (), TransactionBuildResult((), ()))
    assert conn.execute(
        "SELECT b.status,n.duplicate_of_id,r.duplicate_of_id "
        "FROM public.reimbursement_batches b CROSS JOIN public.raw_documents n "
        "CROSS JOIN public.raw_documents r "
        "WHERE b.id=%s AND n.id=%s AND r.id=%s",
        (batch, new_doc, receipt),
    ).fetchone() == ("ready", None, invoice)
    review = _draft(new_doc, "Cafe", flags=("MEAL_SPLIT",))
    update_batch_summary(
        conn, batch, partition, (), TransactionBuildResult((review,), ())
    )
    assert conn.execute(
        "SELECT missing_alerts,status FROM public.reimbursement_batches WHERE id=%s",
        (batch,),
    ).fetchone() == ([], "review_required")


def test_pending_failures_and_tenant_access(process_api: tuple) -> None:
    client, conn, users, user, batch, storage, extractor = process_api
    _, key = _add(conn, user, batch, "application/pdf")
    storage.fail.add(key)
    _add(conn, user, batch, "image/jpeg", _ocr("irrelevant"))
    _add(conn, user, batch, "image/jpeg", _ocr("irrelevant"))
    extractor.fail = "raise"
    result = client.post(f"/api/v1/batches/{batch}/process").json()
    assert (result["deferred_documents"], result["failed_documents"]) == (1, 2)
    assert (
        conn.execute(
            "SELECT ocr_status FROM public.raw_documents WHERE storage_key=%s", (key,)
        ).fetchone()[0]
        == "pending"
    )
    template = _system_template_id(client, users[1])
    other = UUID(
        client.post(
            "/api/v1/batches", json=_body(template), headers=_headers(users[1])
        ).json()["id"]
    )
    assert client.post(f"/api/v1/batches/{other}/process").status_code == 404
    assert (
        TestClient(client.app).post(f"/api/v1/batches/{batch}/process").status_code
        == 401
    )


def test_processing_rollback_hides_failure(process_api: tuple, monkeypatch) -> None:
    import app.batches.processing as service

    client, conn, _, user, batch, _, _ = process_api
    _add(
        conn,
        user,
        batch,
        "image/jpeg",
        _ocr("Invoice", "Merchant: Cafe", "Date: 2026-10-01", "Total: $2.00"),
    )
    monkeypatch.setattr(
        service,
        "sync_transactions",
        lambda *args: (_ for _ in ()).throw(RuntimeError("private error")),
    )
    safe = TestClient(client.app, raise_server_exceptions=False)
    safe.headers.update(_headers(user))
    response = safe.post(f"/api/v1/batches/{batch}/process")
    assert response.status_code == 500 and "private error" not in response.text
    assert conn.execute(
        "SELECT status,error_message FROM public.jobs ORDER BY created_at DESC LIMIT 1"
    ).fetchone() == ("failed", "批次处理失败。")
    assert (
        conn.execute(
            "SELECT ocr_status FROM public.raw_documents WHERE batch_id=%s", (batch,)
        ).fetchone()[0]
        == "pending"
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM public.transactions WHERE batch_id=%s", (batch,)
        ).fetchone()[0]
        == 0
    )


def test_s3_get_success_404_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings

    storage = S3Storage(
        Settings(
            _env_file=None,
            storage_endpoint_url="https://storage.invalid",
            storage_bucket="bucket",
            storage_access_key_id="access",
            storage_secret_access_key="secret",
        )
    )

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self) -> bytes:
            return b"synthetic"

    monkeypatch.setattr("app.storage.urlopen", lambda *a, **k: Response())
    assert storage.get("key") == b"synthetic"
    for error in (
        HTTPError("url", 404, "missing", {}, None),
        HTTPError("url", 500, "failure", {}, None),
        OSError(),
    ):
        monkeypatch.setattr(
            "app.storage.urlopen",
            lambda *a, error=error, **k: (_ for _ in ()).throw(error),
        )
        if isinstance(error, HTTPError) and error.code == 404:
            assert storage.get("key") is None
        else:
            with pytest.raises(StorageError):
                storage.get("key")
