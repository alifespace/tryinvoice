from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg import Connection
from pydantic import SecretStr

from app.rules import seed
from app.rules.seed import load_cmi_default, seed_cmi_default


def test_seed_is_idempotent_and_stores_canonical_template(db_conn: Connection) -> None:
    assert seed_cmi_default(db_conn) == "inserted"
    assert seed_cmi_default(db_conn) == "unchanged"
    rows = db_conn.execute(
        """
        SELECT user_id, is_system_default, name, description, schema_version,
               rules_json
        FROM public.rule_templates
        WHERE rules_json->>'template_code' = 'cmi_default_v1'
        """
    ).fetchall()
    assert rows == [
        (
            None,
            True,
            "CMI 报销规则",
            "CMI 报销规则的系统默认模板。",
            "1.0",
            load_cmi_default().model_dump(mode="json"),
        )
    ]


def test_seed_repairs_drifted_template(db_conn: Connection) -> None:
    assert seed_cmi_default(db_conn) == "inserted"
    db_conn.execute(
        """
        UPDATE public.rule_templates
        SET name = 'damaged', description = 'damaged', schema_version = '0.0',
            rules_json = jsonb_set(
                rules_json, '{categories,0,label}', '"damaged"'::jsonb
            )
        WHERE rules_json->>'template_code' = 'cmi_default_v1'
        """
    )
    assert seed_cmi_default(db_conn) == "updated"
    assert seed_cmi_default(db_conn) == "unchanged"
    row = db_conn.execute(
        """
        SELECT name, description, schema_version, rules_json
        FROM public.rule_templates
        WHERE rules_json->>'template_code' = 'cmi_default_v1'
        """
    ).fetchone()
    assert row == (
        "CMI 报销规则",
        "CMI 报销规则的系统默认模板。",
        "1.0",
        load_cmi_default().model_dump(mode="json"),
    )


def test_seed_commits_on_default_non_autocommit_connection(
    db_conn: Connection, postgres_uri: str
) -> None:
    with psycopg.connect(postgres_uri) as conn:
        assert seed_cmi_default(conn) == "inserted"
    with psycopg.connect(postgres_uri) as reader:
        count = reader.execute(
            """
            SELECT count(*) FROM public.rule_templates
            WHERE user_id IS NULL AND is_system_default
              AND rules_json->>'template_code' = 'cmi_default_v1'
            """
        ).fetchone()[0]
    assert count == 1


def test_default_template_is_visible_but_not_writable_under_rls(
    db_conn: Connection,
) -> None:
    user_id = uuid4()
    db_conn.execute("INSERT INTO auth.users (id) VALUES (%s)", (user_id,))
    assert seed_cmi_default(db_conn) == "inserted"
    db_conn.execute("SET ROLE authenticated")
    db_conn.execute(
        "SELECT set_config('request.jwt.claim.sub', %s, false)", (str(user_id),)
    )
    try:
        assert (
            db_conn.execute(
                """
                SELECT count(*) FROM public.rule_templates
                WHERE rules_json->>'template_code' = 'cmi_default_v1'
                """
            ).fetchone()[0]
            == 1
        )
        changed = db_conn.execute(
            """
            UPDATE public.rule_templates SET name = 'forbidden'
            WHERE rules_json->>'template_code' = 'cmi_default_v1'
            """
        )
        assert changed.rowcount == 0
    finally:
        db_conn.execute("RESET ROLE")


def test_seed_cli_requires_database_url_without_leaking_it(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        seed, "get_settings", lambda: SimpleNamespace(database_url=None)
    )
    assert seed.main() == 1
    captured = capsys.readouterr()
    assert "DATABASE_URL" in captured.err
    assert "postgresql://" not in captured.out + captured.err


def test_seed_cli_success_does_not_print_database_url(
    db_conn: Connection, monkeypatch, capsys
) -> None:
    connection_string = "postgresql://private.example/tryinvoice"
    monkeypatch.setattr(
        seed,
        "get_settings",
        lambda: SimpleNamespace(database_url=SecretStr(connection_string)),
    )
    monkeypatch.setattr(seed.psycopg, "connect", lambda _: db_conn)
    assert seed.main() == 0
    captured = capsys.readouterr()
    assert "inserted" in captured.out
    assert connection_string not in captured.out + captured.err


def test_seed_cli_database_error_is_redacted(monkeypatch, capsys) -> None:
    connection_string = "postgresql://private.example/tryinvoice"
    monkeypatch.setattr(
        seed,
        "get_settings",
        lambda: SimpleNamespace(database_url=SecretStr(connection_string)),
    )

    def fail_connect(_: str) -> None:
        raise psycopg.OperationalError(connection_string)

    monkeypatch.setattr(seed.psycopg, "connect", fail_connect)
    assert seed.main() == 1
    captured = capsys.readouterr()
    assert "数据库错误" in captured.err
    assert connection_string not in captured.out + captured.err
