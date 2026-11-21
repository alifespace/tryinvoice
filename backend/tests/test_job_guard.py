from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import psycopg
from psycopg import Connection

from app.batches.jobs import JobConflict, start_job
from app.rules.transactions import TransactionDraft
from tests.test_auth_batches_api import _body, _headers, _system_template_id

pytest_plugins = ("tests.test_process_batch", "tests.test_export_api")


def _running_job(conn: Connection, user: UUID, batch: UUID, job_type: str) -> UUID:
    return conn.execute(
        "INSERT INTO public.jobs (user_id,batch_id,job_type,status) "
        "VALUES (%s,%s,%s,'running') RETURNING id",
        (user, batch, job_type),
    ).fetchone()[0]


def test_running_process_returns_sanitized_conflict(process_api: tuple) -> None:
    client, conn, _, user, batch, _, extractor = process_api
    existing = _running_job(conn, user, batch, "process_batch")
    response = client.post(f"/api/v1/batches/{batch}/process")

    assert response.status_code == 409
    assert response.json() == {"detail": "任务正在执行，请稍后重试"}
    assert str(existing) not in response.text and "JobConflict" not in response.text
    assert extractor.calls == 0
    assert (
        conn.execute(
            "SELECT count(*) FROM public.jobs WHERE batch_id=%s", (batch,)
        ).fetchone()[0]
        == 1
    )


def test_running_export_returns_conflict(export_api: tuple) -> None:
    client, conn, _, user, batch, storage = export_api
    _running_job(conn, user, batch, "export_bundle")

    response = client.post(f"/api/v1/batches/{batch}/export", headers=_headers(user))

    assert response.status_code == 409
    assert response.json() == {"detail": "任务正在执行，请稍后重试"}
    assert storage.puts == []
    assert (
        conn.execute(
            "SELECT count(*) FROM public.jobs WHERE batch_id=%s", (batch,)
        ).fetchone()[0]
        == 1
    )


def test_stale_process_is_failed_before_new_job(process_api: tuple) -> None:
    client, conn, _, user, batch, _, _ = process_api
    stale = _running_job(conn, user, batch, "process_batch")
    conn.execute(
        "UPDATE public.jobs SET updated_at=now()-interval '31 minutes' WHERE id=%s",
        (stale,),
    )

    response = client.post(f"/api/v1/batches/{batch}/process")

    assert response.status_code == 200
    assert conn.execute(
        "SELECT status,error_message FROM public.jobs WHERE id=%s", (stale,)
    ).fetchone() == ("failed", "任务超时。")
    assert conn.execute(
        "SELECT status FROM public.jobs WHERE id=%s", (response.json()["job_id"],)
    ).fetchone() == ("succeeded",)


def test_conflict_still_recovers_stale_job(process_api: tuple) -> None:
    client, conn, _, user, batch, _, extractor = process_api
    stale = _running_job(conn, user, batch, "process_batch")
    fresh = _running_job(conn, user, batch, "process_batch")
    conn.execute(
        "UPDATE public.jobs SET updated_at=now()-interval '31 minutes' WHERE id=%s",
        (stale,),
    )

    response = client.post(f"/api/v1/batches/{batch}/process")

    assert response.status_code == 409
    assert extractor.calls == 0
    assert conn.execute(
        "SELECT status,error_message FROM public.jobs WHERE id=%s", (stale,)
    ).fetchone() == ("failed", "任务超时。")
    assert conn.execute(
        "SELECT status,error_message FROM public.jobs WHERE id=%s", (fresh,)
    ).fetchone() == ("running", None)
    assert conn.execute(
        "SELECT count(*) FROM public.jobs WHERE batch_id=%s", (batch,)
    ).fetchone() == (2,)


def test_failed_job_can_be_retried(process_api: tuple, monkeypatch) -> None:
    import app.batches.processing as processing

    client, conn, _, _, batch, _, _ = process_api
    original = processing.sync_transactions
    attempts = 0

    def fail_once(
        connection: Connection,
        user_id: UUID,
        batch_id: UUID,
        drafts: Sequence[TransactionDraft],
    ) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("synthetic transaction failure")
        original(connection, user_id, batch_id, drafts)

    monkeypatch.setattr(processing, "sync_transactions", fail_once)
    path = f"/api/v1/batches/{batch}/process"
    failed = client.post(path)
    assert failed.status_code == 500
    assert "synthetic transaction failure" not in failed.text
    assert conn.execute(
        "SELECT status,error_message FROM public.jobs WHERE batch_id=%s", (batch,)
    ).fetchone() == ("failed", "批次处理失败。")

    retried = client.post(path)
    assert retried.status_code == 200
    assert conn.execute(
        "SELECT status FROM public.jobs WHERE batch_id=%s ORDER BY created_at,id",
        (batch,),
    ).fetchall() == [("failed",), ("succeeded",)]


def test_other_batch_and_job_type_do_not_conflict(process_api: tuple) -> None:
    client, conn, _, user, batch, _, _ = process_api
    template = _system_template_id(client, user)
    other_batch = UUID(
        client.post(
            "/api/v1/batches",
            json=_body(template),
            headers=_headers(user),
        ).json()["id"]
    )
    first = start_job(conn, user, batch, "process_batch")

    export = start_job(conn, user, batch, "export_bundle")
    other = start_job(conn, user, other_batch, "process_batch")

    assert len({first, export, other}) == 3


def test_concurrent_start_has_one_winner(process_api: tuple, postgres_uri: str) -> None:
    _, _, _, user, batch, _, _ = process_api
    gate = Barrier(2)

    def attempt() -> str:
        with psycopg.connect(postgres_uri, autocommit=True) as conn:
            gate.wait(timeout=10)
            try:
                start_job(conn, user, batch, "process_batch")
            except JobConflict:
                return "conflict"
            return "started"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(attempt) for _ in range(2)]
        results = sorted(future.result(timeout=20) for future in futures)

    assert results == ["conflict", "started"]
