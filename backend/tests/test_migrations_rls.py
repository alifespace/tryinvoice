from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import Connection
from pydantic import SecretStr

from app.db import migrate
from app.db.migrate import apply_migrations

_UPDATE_BATCH = (
    "UPDATE public.reimbursement_batches SET title = 'changed' WHERE id = %s"
)
_DELETE_DOCUMENT = "DELETE FROM public.raw_documents WHERE id = %s"
_UPDATE_TEMPLATE = "UPDATE public.rule_templates SET name = 'changed' WHERE id = %s"
_DELETE_TEMPLATE = "DELETE FROM public.rule_templates WHERE id = %s"


@contextmanager
def as_user(conn: Connection, user_id: UUID | None) -> Iterator[None]:
    conn.execute("SET ROLE authenticated")
    conn.execute(
        "SELECT set_config('request.jwt.claim.sub', %s, false)", (str(user_id or ""),)
    )
    try:
        yield
    finally:
        conn.execute("RESET ROLE")


def _add_users(conn: Connection, *user_ids: UUID) -> None:
    conn.execute(
        "INSERT INTO auth.users (id) SELECT unnest(%s::uuid[])", (list(user_ids),)
    )


def _count(conn: Connection, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM public.{table}").fetchone()[0]


def _rowcount(conn: Connection, statement: str, *values: UUID) -> int:
    return conn.execute(statement, values).rowcount


def _names(conn: Connection, statement: str) -> set[str]:
    return {row[0] for row in conn.execute(statement).fetchall()}


def _add_template(conn: Connection, user_id: UUID | None = None) -> UUID:
    return conn.execute(
        "INSERT INTO public.rule_templates (user_id, is_system_default, name, "
        "rules_json) VALUES (%s, %s, 'test', '{}'::jsonb) RETURNING id",
        (user_id, user_id is None),
    ).fetchone()[0]


def _add_user_data(
    conn: Connection, user_id: UUID, template_id: UUID
) -> tuple[UUID, UUID]:
    with as_user(conn, user_id):
        batch_id, document_id = conn.execute(
            """
            WITH batch AS (
                INSERT INTO public.reimbursement_batches
                    (user_id, template_id, expense_type, title, batch_date)
                VALUES (%s, %s, 'meal', 'test', '2026-01-01') RETURNING id
            ), doc AS (
                INSERT INTO public.raw_documents
                    (user_id, batch_id, original_filename, mime_type,
                     file_size_bytes, sha256_hash, storage_key)
                SELECT %s, batch.id, 'receipt.jpg', 'image/jpeg', 1, %s, 'raw/test'
                FROM batch RETURNING id
            ), tx AS (
                INSERT INTO public.transactions
                    (user_id, batch_id, category, region_scope, region_detail,
                     tx_date, merchant, currency, original_amount, claim_amount,
                     payment_status)
                SELECT %s, batch.id, 'EAT', 'in_scope', '美洲', '2026-01-01',
                       'test merchant', 'USD', 1.00, 1.00, 'paired'
                FROM batch RETURNING id
            ), link AS (
                INSERT INTO public.transaction_documents
                    (transaction_id, raw_document_id, user_id, link_role)
                SELECT tx.id, doc.id, %s, 'primary' FROM tx, doc
                RETURNING transaction_id
            )
            SELECT batch.id, doc.id FROM batch, doc, link
            """,
            (user_id, template_id, user_id, str(uuid4()), user_id, user_id),
        ).fetchone()
    return batch_id, document_id


def test_migrations(db_conn, monkeypatch, capsys):
    db_conn.execute("DELETE FROM public.schema_migrations")
    assert apply_migrations(db_conn) == ["0001_init_schema.sql"]
    assert apply_migrations(db_conn) == []
    db_conn.execute(migrate.MIGRATIONS_DIR.joinpath("0001_init_schema.sql").read_text())
    secret = "unused-test-dsn"
    settings = SimpleNamespace(database_url=SecretStr(secret))
    monkeypatch.setattr(migrate, "get_settings", lambda: settings)

    def fail_connect(_: str) -> None:
        raise psycopg.OperationalError("not connected")

    monkeypatch.setattr(migrate.psycopg, "connect", fail_connect)
    assert migrate.main() == 1 and secret not in capsys.readouterr().err
    monkeypatch.setattr(migrate.psycopg, "connect", lambda _: db_conn)
    monkeypatch.setattr(migrate, "apply_migrations", lambda _: ["0001_test.sql"])
    assert migrate.main() == 0 and "0001_test.sql" in capsys.readouterr().out


def test_per_file_transactions(postgres_uri, tmp_path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    good, bad = "0001_good_probe.sql", "0002_bad_probe.sql"
    for filename, sql in (
        (good, "CREATE TABLE public.migration_commit_probe (id INTEGER PRIMARY KEY);"),
        (bad, "SELECT 1 / 0;"),
    ):
        (migrations_dir / filename).write_text(sql, encoding="utf-8")

    with pytest.raises(psycopg.errors.DivisionByZero):
        with psycopg.connect(postgres_uri) as conn:
            apply_migrations(conn, migrations_dir)
    with psycopg.connect(postgres_uri, autocommit=True) as conn:
        assert conn.execute(
            "SELECT to_regclass('public.migration_commit_probe')"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT filename FROM public.schema_migrations WHERE filename IN (%s, %s)",
            (good, bad),
        ).fetchall() == [(good,)]


def test_schema_shape_and_security(db_conn: Connection) -> None:
    expected = {
        "user_profiles": 6,
        "rule_templates": 9,
        "reimbursement_batches": 13,
        "raw_documents": 16,
        "transactions": 21,
        "transaction_documents": 5,
        "jobs": 10,
    }
    columns = db_conn.execute(
        "SELECT table_name, count(*), max(is_nullable) FILTER "
        "(WHERE column_name='user_id') FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name <> 'schema_migrations' "
        "GROUP BY table_name"
    ).fetchall()
    assert {table: count for table, count, _ in columns} == expected
    assert {table: nullable for table, _, nullable in columns} == {
        table: "YES" if table == "rule_templates" else "NO" for table in expected
    }
    assert _names(
        db_conn,
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relrowsecurity",
    ) == set(expected)
    assert _names(
        db_conn,
        "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
        "AND indexname NOT LIKE '%_pkey'",
    ) == set(
        "idx_rule_templates_user idx_batches_user_date "
        "idx_raw_docs_batch_hash idx_raw_docs_user_batch "
        "idx_transactions_user_batch idx_jobs_user_batch".split()
    )


def test_rls_user_isolation(db_conn: Connection) -> None:
    user_a, user_b = uuid4(), uuid4()
    _add_users(db_conn, user_a, user_b)
    template_id = _add_template(db_conn)
    _add_user_data(db_conn, user_a, template_id)
    batch_b, document_b = _add_user_data(db_conn, user_b, template_id)

    with as_user(db_conn, user_a):
        assert _count(db_conn, "reimbursement_batches") == 1
        assert _count(db_conn, "raw_documents") == 1
        assert _count(db_conn, "transaction_documents") == 1
        assert _rowcount(db_conn, _UPDATE_BATCH, batch_b) == 0
        assert _rowcount(db_conn, _DELETE_DOCUMENT, document_b) == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db_conn.execute(
                """
                INSERT INTO public.reimbursement_batches
                    (user_id, template_id, expense_type, title, batch_date)
                VALUES (%s, %s, 'meal', 'forbidden', '2026-01-01')
                """,
                (user_b, template_id),
            )

    with as_user(db_conn, None):
        assert _count(db_conn, "reimbursement_batches") == 0


def test_template_visibility(db_conn: Connection) -> None:
    user_a, user_b = uuid4(), uuid4()
    _add_users(db_conn, user_a, user_b)
    default_id = _add_template(db_conn)

    for user_id in (user_a, user_b):
        with as_user(db_conn, user_id):
            assert _count(db_conn, "rule_templates") == 1
            assert _rowcount(db_conn, _UPDATE_TEMPLATE, default_id) == 0
            assert _rowcount(db_conn, _DELETE_TEMPLATE, default_id) == 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _add_template(db_conn)

    with as_user(db_conn, user_a):
        _add_template(db_conn, user_a)
        assert _count(db_conn, "rule_templates") == 2
    with as_user(db_conn, user_b):
        assert _count(db_conn, "rule_templates") == 1
