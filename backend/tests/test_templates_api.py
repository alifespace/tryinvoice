import copy
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg import Connection
from pydantic import SecretStr

from app.api.deps import get_conn, get_current_user_id
from app.main import create_app
from app.rules.seed import load_cmi_default, seed_cmi_default


@pytest.fixture
def template_api(db_conn: Connection) -> Iterator[tuple[TestClient, list[UUID]]]:
    user_ids = [uuid4(), uuid4()]
    for user_id in user_ids:
        db_conn.execute("INSERT INTO auth.users (id) VALUES (%s)", (user_id,))
    seed_cmi_default(db_conn)
    application = create_app()
    application.dependency_overrides[get_conn] = lambda: db_conn
    application.dependency_overrides[get_current_user_id] = lambda: user_ids[0]
    with TestClient(application) as client:
        yield client, user_ids


def _rules() -> dict[str, Any]:
    return load_cmi_default().model_dump(mode="json")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/v1/templates", None),
        ("post", "/api/v1/templates", {"name": "Mine", "rules": _rules()}),
        ("post", f"/api/v1/templates/{uuid4()}/clone", None),
        ("patch", f"/api/v1/templates/{uuid4()}", {"name": "Mine"}),
    ],
)
def test_template_routes_reject_unconfigured_auth(
    method: str, path: str, body: dict[str, object] | None
) -> None:
    client = TestClient(create_app())
    response = (
        getattr(client, method)(path, json=body)
        if body is not None
        else getattr(client, method)(path)
    )
    assert response.status_code == 401


def test_list_templates_is_tenant_scoped_and_omits_user_id(
    template_api: tuple[TestClient, list[UUID]],
) -> None:
    client, users = template_api
    created = client.post("/api/v1/templates", json={"name": "A", "rules": _rules()})
    assert created.status_code == 201
    own_id = created.json()["id"]
    response = client.get("/api/v1/templates")
    assert response.status_code == 200
    system_id = next(
        item["id"] for item in response.json() if item["is_system_default"]
    )
    assert {item["id"] for item in response.json()} == {
        own_id,
        system_id,
    }
    assert all("user_id" not in item for item in response.json())
    assert (
        next(item for item in response.json() if item["is_system_default"])["rules"][
            "template_code"
        ]
        == "cmi_default_v1"
    )
    client.app.dependency_overrides[get_current_user_id] = lambda: users[1]
    assert all(item["id"] != own_id for item in client.get("/api/v1/templates").json())


@pytest.mark.parametrize(
    "rules",
    [
        {**_rules(), "unknown": True},
        {
            **_rules(),
            "naming_and_export": {
                **_rules()["naming_and_export"],
                "standard_pattern": "{category:^10}.{ext}",
            },
        },
    ],
)
def test_create_validates_rules_before_inserting(
    template_api: tuple[TestClient, list[UUID]], rules: dict[str, object]
) -> None:
    client, _ = template_api
    before = client.get("/api/v1/templates").json()
    response = client.post(
        "/api/v1/templates", json={"name": "  New  ", "rules": rules}
    )
    assert response.status_code == 422
    assert client.get("/api/v1/templates").json() == before


def test_create_template_persists_owner_and_normalizes_name(
    template_api: tuple[TestClient, list[UUID]], db_conn: Connection
) -> None:
    client, users = template_api
    response = client.post(
        "/api/v1/templates", json={"name": "  New  ", "rules": _rules()}
    )
    assert response.status_code == 201
    item = response.json()
    assert item["name"] == "New" and item["schema_version"] == "1.0"
    assert item["rules"]["template_code"] == "cmi_default_v1"
    row = db_conn.execute(
        """
        SELECT user_id, is_system_default, schema_version
        FROM public.rule_templates WHERE id = %s
        """,
        (item["id"],),
    ).fetchone()
    assert row == (users[0], False, "1.0")


def test_clone_default_and_own_template_and_hide_other_tenants(
    template_api: tuple[TestClient, list[UUID]], db_conn: Connection
) -> None:
    client, users = template_api
    system = client.get("/api/v1/templates").json()[0]
    system_id = system["id"]
    cloned = client.post(f"/api/v1/templates/{system_id}/clone")
    assert cloned.status_code == 201
    item = cloned.json()
    assert item["name"].endswith("（副本）") and item["is_system_default"] is False
    assert item["rules"]["template_code"].startswith("custom_")
    assert len(item["rules"]["template_code"]) == 23
    expected_rules = copy.deepcopy(system["rules"])
    expected_rules["template_code"] = item["rules"]["template_code"]
    assert item["rules"] == expected_rules
    assert db_conn.execute(
        "SELECT user_id, is_system_default FROM public.rule_templates WHERE id = %s",
        (item["id"],),
    ).fetchone() == (users[0], False)
    assert (
        next(
            row
            for row in client.get("/api/v1/templates").json()
            if row["id"] == system_id
        )
        == system
    )
    own_clone = client.post(
        f"/api/v1/templates/{item['id']}/clone", json={"name": "Copy"}
    )
    assert own_clone.status_code == 201 and own_clone.json()["name"] == "Copy"
    client.app.dependency_overrides[get_current_user_id] = lambda: users[1]
    assert client.post(f"/api/v1/templates/{item['id']}/clone").status_code == 404
    assert client.post(f"/api/v1/templates/{uuid4()}/clone").status_code == 404


def test_patch_updates_partial_fields_and_rejects_empty_or_invalid_rules(
    template_api: tuple[TestClient, list[UUID]], db_conn: Connection
) -> None:
    client, _ = template_api
    created = client.post(
        "/api/v1/templates",
        json={"name": "Before", "description": "Keep", "rules": _rules()},
    ).json()
    template_id = created["id"]
    db_conn.execute(
        """
        UPDATE public.rule_templates
        SET updated_at = now() - interval '1 minute'
        WHERE id = %s
        """,
        (template_id,),
    )
    changed = copy.deepcopy(_rules())
    changed["categories"][0]["label"] = "Updated"
    response = client.patch(
        f"/api/v1/templates/{template_id}", json={"name": "After", "rules": changed}
    )
    assert response.status_code == 200
    assert (
        response.json()["name"] == "After" and response.json()["description"] == "Keep"
    )
    assert response.json()["rules"]["categories"][0]["label"] == "Updated"
    assert response.json()["updated_at"] > created["updated_at"]
    assert client.patch(f"/api/v1/templates/{template_id}", json={}).status_code == 422
    invalid = copy.deepcopy(_rules())
    invalid["unexpected"] = True
    assert (
        client.patch(
            f"/api/v1/templates/{template_id}", json={"rules": invalid}
        ).status_code
        == 422
    )
    unchanged = next(
        item
        for item in client.get("/api/v1/templates").json()
        if item["id"] == template_id
    )
    assert unchanged["name"] == "After" and unchanged["rules"] == changed


def test_system_and_other_users_cannot_patch_templates(
    template_api: tuple[TestClient, list[UUID]], db_conn: Connection
) -> None:
    client, users = template_api
    system = client.get("/api/v1/templates").json()[0]
    before = db_conn.execute(
        "SELECT name, rules_json, updated_at FROM public.rule_templates WHERE id = %s",
        (system["id"],),
    ).fetchone()
    denied = client.patch(f"/api/v1/templates/{system['id']}", json={"name": "Changed"})
    assert (
        denied.status_code == 403 and denied.json()["detail"] == "系统默认模板不可修改"
    )
    assert (
        db_conn.execute(
            """
            SELECT name, rules_json, updated_at
            FROM public.rule_templates WHERE id = %s
            """,
            (system["id"],),
        ).fetchone()
        == before
    )
    own = client.post(
        "/api/v1/templates", json={"name": "Private", "rules": _rules()}
    ).json()
    client.app.dependency_overrides[get_current_user_id] = lambda: users[1]
    assert (
        client.patch(
            f"/api/v1/templates/{own['id']}", json={"name": "Changed"}
        ).status_code
        == 404
    )
    assert db_conn.execute(
        "SELECT name FROM public.rule_templates WHERE id = %s", (own["id"],)
    ).fetchone() == ("Private",)


def test_database_dependency_rejects_missing_url_and_closes_connection(
    monkeypatch,
) -> None:
    from app.api import deps

    monkeypatch.setattr(
        deps, "get_settings", lambda: SimpleNamespace(database_url=None)
    )
    with pytest.raises(HTTPException) as error:
        next(deps.get_conn())
    assert error.value.status_code == 503

    class ConnectionContext:
        closed = False

        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_: object) -> None:
            self.closed = True

    connection_context = ConnectionContext()
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(database_url=SecretStr("postgresql://local/test")),
    )
    monkeypatch.setattr(
        deps.psycopg, "connect", lambda *_args, **_kwargs: connection_context
    )
    connection = deps.get_conn()
    next(connection)
    with pytest.raises(StopIteration):
        next(connection)
    assert connection_context.closed
