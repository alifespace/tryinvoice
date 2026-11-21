from collections.abc import Iterator

import psycopg
import pytest
from pixeltable_pgserver import get_server
from psycopg import Connection


@pytest.fixture(scope="session")
def postgres_uri(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    server = get_server(tmp_path_factory.mktemp("pg") / "data", cleanup_mode="delete")
    yield server.get_uri()
    server.cleanup()


@pytest.fixture
def db_conn(postgres_uri: str) -> Iterator[Connection]:
    with psycopg.connect(postgres_uri, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute("DROP SCHEMA IF EXISTS auth CASCADE")
        conn.execute("DROP ROLE IF EXISTS authenticated")
        conn.execute("CREATE SCHEMA auth")
        conn.execute("CREATE TABLE auth.users (id UUID PRIMARY KEY)")
        conn.execute(
            "CREATE FUNCTION auth.uid() RETURNS UUID LANGUAGE sql STABLE "
            "AS $$ SELECT nullif(current_setting('request.jwt.claim.sub', true), "
            "'')::uuid $$"
        )
        conn.execute("CREATE ROLE authenticated NOLOGIN")

        from app.db.migrate import apply_migrations

        apply_migrations(conn)
        conn.execute("GRANT USAGE ON SCHEMA public, auth TO authenticated")
        conn.execute("GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated")
        conn.execute("GRANT SELECT ON auth.users TO authenticated")
        yield conn
