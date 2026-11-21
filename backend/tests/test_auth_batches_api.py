from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from app.api.deps import get_conn
from app.config import Settings
from app.main import create_app
from app.rules.seed import load_cmi_default, seed_cmi_default

_TEST_SECRET = "test-secret-only-not-a-real-credential-123456"


def _set_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    secret: str | None = _TEST_SECRET,
    app_env: str = "test",
    local_user_id: UUID | None = None,
) -> None:
    settings = Settings(
        _env_file=None,
        supabase_jwt_secret=SecretStr(secret) if secret is not None else None,
        supabase_jwt_issuer="https://synthetic.supabase.co/auth/v1",
        supabase_allow_legacy_hs256=True,
        app_env=app_env,
        local_dev_user_id=local_user_id,
    )
    monkeypatch.setattr("app.api.deps.get_settings", lambda: settings)


def _token(user_id: UUID, *, secret: str = _TEST_SECRET, **claims: Any) -> str:
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "aud": "authenticated",
        "iss": "https://synthetic.supabase.co/auth/v1",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        **claims,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _headers(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


@pytest.fixture
def batch_api(
    db_conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, list[UUID]]]:
    users = [uuid4(), uuid4()]
    for user_id in users:
        db_conn.execute("INSERT INTO auth.users (id) VALUES (%s)", (user_id,))
    seed_cmi_default(db_conn)
    _set_settings(monkeypatch)
    application = create_app()
    application.dependency_overrides[get_conn] = lambda: db_conn
    with TestClient(application) as client:
        yield client, users


def _body(template_id: UUID, **changes: Any) -> dict[str, Any]:
    return {
        "template_id": str(template_id),
        "expense_type": "business_trip",
        "title": "  Conference  ",
        "batch_date": "2026-10-09",
        **changes,
    }


def _system_template_id(client: TestClient, user_id: UUID) -> UUID:
    response = client.get("/api/v1/templates", headers=_headers(user_id))
    assert response.status_code == 200
    return UUID(
        next(item["id"] for item in response.json() if item["is_system_default"])
    )


@pytest.mark.parametrize(
    "case",
    [
        "expired",
        "wrong_signature",
        "wrong_audience",
        "missing_sub",
        "bad_sub",
        "none",
        "malformed",
        "no_secret",
    ],
)
def test_invalid_bearer_is_401_without_local_fallback(
    batch_api: tuple[TestClient, list[UUID]],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    client, users = batch_api
    _set_settings(monkeypatch, app_env="local", local_user_id=users[0])
    claims = jwt.decode(_token(users[0]), options={"verify_signature": False})
    claims.update(
        {
            "expired": {"exp": datetime.now(UTC) - timedelta(minutes=1)},
            "wrong_audience": {"aud": "another-audience"},
            "bad_sub": {"sub": "not-a-uuid"},
        }.get(case, {})
    )
    if case == "missing_sub":
        claims.pop("sub")
    algorithm, secret = {
        "none": ("none", ""),
        "wrong_signature": ("HS256", "different-test-secret-not-a-real-credential"),
    }.get(case, ("HS256", _TEST_SECRET))
    token = (
        "malformed.test.token"
        if case == "malformed"
        else jwt.encode(claims, secret, algorithm=algorithm)
    )
    if case == "no_secret":
        _set_settings(monkeypatch, secret=None, app_env="local", local_user_id=users[0])

    response = client.get(
        "/api/v1/batches", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert token not in response.text


def test_incomplete_bearer_does_not_use_local_fallback(
    batch_api: tuple[TestClient, list[UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, users = batch_api
    _set_settings(monkeypatch, app_env="local", local_user_id=users[0])
    response = client.get("/api/v1/batches", headers={"Authorization": "Bearer"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_valid_jwt_creates_batch_with_owner_and_database_defaults(
    batch_api: tuple[TestClient, list[UUID]], db_conn: Connection
) -> None:
    client, users = batch_api
    template_id = _system_template_id(client, users[0])
    response = client.post(
        "/api/v1/batches",
        json=_body(template_id),
        headers=_headers(users[0]),
    )
    assert response.status_code == 201
    item = response.json()
    assert item["title"] == "Conference" and item["status"] == "draft"
    assert "user_id" not in item
    assert db_conn.execute(
        "SELECT user_id, status FROM public.reimbursement_batches WHERE id = %s",
        (item["id"],),
    ).fetchone() == (users[0], "draft")


def test_local_single_user_fallback_requires_local_environment_and_user(
    batch_api: tuple[TestClient, list[UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, users = batch_api
    template_id = _system_template_id(client, users[0])
    _set_settings(monkeypatch, secret=None, app_env="local", local_user_id=users[0])
    response = client.post("/api/v1/batches", json=_body(template_id))
    assert response.status_code == 201
    assert response.json()["id"]

    for app_env, local_user_id in [("test", users[0]), ("local", None)]:
        _set_settings(
            monkeypatch,
            secret=None,
            app_env=app_env,
            local_user_id=local_user_id,
        )
        assert client.get("/api/v1/batches").status_code == 401


def test_create_rejects_missing_or_private_template_and_invalid_body(
    batch_api: tuple[TestClient, list[UUID]],
    db_conn: Connection,
) -> None:
    client, users = batch_api
    visible_template = _system_template_id(client, users[0])
    private_template_id = uuid4()
    db_conn.execute(
        """
        INSERT INTO public.rule_templates (id, user_id, name, rules_json)
        VALUES (%s, %s, 'Private', %s)
        """,
        (
            private_template_id,
            users[1],
            Jsonb(load_cmi_default().model_dump(mode="json")),
        ),
    )
    before = db_conn.execute(
        "SELECT count(*) FROM public.reimbursement_batches"
    ).fetchone()[0]
    for template_id in (private_template_id, uuid4()):
        response = client.post(
            "/api/v1/batches",
            json=_body(template_id),
            headers=_headers(users[0]),
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "模板不存在或不可见"
    for changes in ({"expense_type": "other"}, {"title": "  "}):
        response = client.post(
            "/api/v1/batches",
            json=_body(visible_template, **changes),
            headers=_headers(users[0]),
        )
        assert response.status_code == 422
    assert (
        db_conn.execute("SELECT count(*) FROM public.reimbursement_batches").fetchone()[
            0
        ]
        == before
    )


def test_batch_list_is_tenant_scoped_sorted_and_paginated(
    batch_api: tuple[TestClient, list[UUID]],
) -> None:
    client, users = batch_api
    template_id = _system_template_id(client, users[0])
    dates = ["2026-10-01", "2026-10-03", "2026-10-02"]
    for batch_date in dates:
        response = client.post(
            "/api/v1/batches",
            json=_body(template_id, batch_date=batch_date),
            headers=_headers(users[0]),
        )
        assert response.status_code == 201

    page = client.get("/api/v1/batches?limit=2&offset=1", headers=_headers(users[0]))
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert [item["batch_date"] for item in page.json()["items"]] == [
        "2026-10-02",
        "2026-10-01",
    ]
    other = client.get("/api/v1/batches", headers=_headers(users[1])).json()
    assert other["items"] == [] and other["total"] == 0
    for query in ("limit=0", "limit=101", "offset=-1"):
        assert (
            client.get(
                f"/api/v1/batches?{query}", headers=_headers(users[0])
            ).status_code
            == 422
        )


def test_batch_delete_is_owner_scoped_and_cascades_database_rows(
    batch_api: tuple[TestClient, list[UUID]],
    db_conn: Connection,
) -> None:
    client, users = batch_api
    template_id = _system_template_id(client, users[0])
    created = client.post(
        "/api/v1/batches", json=_body(template_id), headers=_headers(users[0])
    )
    batch_id = UUID(created.json()["id"])
    document_id = uuid4()
    db_conn.execute(
        """
        INSERT INTO public.raw_documents
            (id, user_id, batch_id, original_filename, mime_type,
             file_size_bytes, sha256_hash, storage_key)
        VALUES (%s, %s, %s, 'receipt.jpg', 'image/jpeg', 10, 'hash', 'key')
        """,
        (document_id, users[0], batch_id),
    )
    deleted = client.delete(f"/api/v1/batches/{batch_id}", headers=_headers(users[0]))
    assert deleted.status_code == 204 and not deleted.content
    assert (
        db_conn.execute(
            "SELECT count(*) FROM public.raw_documents WHERE id = %s", (document_id,)
        ).fetchone()[0]
        == 0
    )

    other_batch = client.post(
        "/api/v1/batches", json=_body(template_id), headers=_headers(users[0])
    )
    other_id = UUID(other_batch.json()["id"])
    denied = client.delete(f"/api/v1/batches/{other_id}", headers=_headers(users[1]))
    missing = client.delete(f"/api/v1/batches/{uuid4()}", headers=_headers(users[0]))
    assert denied.status_code == missing.status_code == 404
    assert (
        db_conn.execute(
            "SELECT count(*) FROM public.reimbursement_batches WHERE id = %s",
            (other_id,),
        ).fetchone()[0]
        == 1
    )
