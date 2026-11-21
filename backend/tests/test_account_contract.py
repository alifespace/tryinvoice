import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from psycopg import Connection
from psycopg.types.json import Jsonb

from app.api.deps import get_storage
from app.config import Settings
from app.contract import build_openapi, validate_openapi
from app.rules.seed import load_cmi_default
from app.storage import S3Storage
from tests.test_auth_batches_api import _headers
from tests.test_documents_storage import FakeStorage

pytest_plugins = ("tests.test_auth_batches_api",)


def _seed_owner(conn: Connection, user: UUID, rules: Jsonb) -> UUID:
    batch = conn.execute(
        "WITH t AS (INSERT INTO public.rule_templates(user_id,name,rules_json) "
        "VALUES(%s,'Private',%s) RETURNING id) "
        "INSERT INTO public.reimbursement_batches(user_id,template_id,expense_type,"
        "title,batch_date) SELECT %s,id,'meal','Test','2026-11-21' FROM t RETURNING id",
        (user, rules, user),
    ).fetchone()[0]
    conn.execute("INSERT INTO public.user_profiles (user_id) VALUES (%s)", (user,))
    conn.execute(
        "INSERT INTO public.raw_documents(user_id,batch_id,original_filename,"
        "mime_type,file_size_bytes,sha256_hash,storage_key) "
        "VALUES (%s,%s,'a','image/jpeg',1,%s,%s),"
        "(%s,%s,'b','image/jpeg',1,%s,'legacy-key')",
        (user, batch, "a" * 64, f"{user}/{batch}/raw/a.jpg", user, batch, "b" * 64),
    )
    return batch


def _owner_counts(conn: Connection, user: UUID) -> tuple[int, ...]:
    return conn.execute(
        """SELECT (SELECT count(*) FROM public.reimbursement_batches WHERE user_id=%s),
        (SELECT count(*) FROM public.raw_documents WHERE user_id=%s),
        (SELECT count(*) FROM public.rule_templates WHERE user_id=%s),
        (SELECT count(*) FROM public.user_profiles WHERE user_id=%s)""",
        (user,) * 4,
    ).fetchone()


def test_account_delete_storage_order_and_isolation(
    batch_api: tuple[TestClient, list[UUID]], db_conn: Connection
) -> None:
    client, users = batch_api
    storage = FakeStorage()
    client.app.dependency_overrides[get_storage] = lambda: storage
    assert client.delete("/api/v1/account").status_code == 401
    system = db_conn.execute(
        "SELECT id FROM public.rule_templates WHERE is_system_default"
    ).fetchone()[0]
    rules = Jsonb(load_cmi_default().model_dump(mode="json"))
    batches = [_seed_owner(db_conn, user, rules) for user in users]
    user = users[0]
    client.headers.update(_headers(user))
    storage.fail_delete = True
    assert client.delete("/api/v1/account").status_code == 502
    assert _owner_counts(db_conn, user) == (1, 2, 1, 1)
    client.app.dependency_overrides[get_storage] = lambda: S3Storage(
        Settings(_env_file=None)
    )
    assert client.delete("/api/v1/account").status_code == 503
    client.app.dependency_overrides[get_storage] = lambda: storage
    storage.fail_delete = False
    storage.deletes.clear()
    assert client.delete("/api/v1/account").status_code == 204
    assert client.delete("/api/v1/account").status_code == 204
    assert storage.deletes == [f"{user}/{batches[0]}/raw/a.jpg"]
    assert _owner_counts(db_conn, user) == (0, 0, 0, 0)
    assert _owner_counts(db_conn, users[1]) == (1, 2, 1, 1)
    assert db_conn.execute(
        "SELECT id FROM public.rule_templates WHERE id=%s", (system,)
    ).fetchone() == (system,)
    assert db_conn.execute("SELECT id FROM auth.users WHERE id=%s", (user,)).fetchone()
    schema = build_openapi()
    validate_openapi(schema)
    path = Path(__file__).resolve().parents[2] / "contracts/openapi.json"
    expected = (
        json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    assert path.read_text(encoding="utf-8") == expected
    assert schema["paths"]["/api/v1/account"]["delete"]["security"]
