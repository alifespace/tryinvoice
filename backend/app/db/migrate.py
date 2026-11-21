import sys
from pathlib import Path

import psycopg
from psycopg import Connection
from psycopg.pq import TransactionStatus

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def apply_migrations(
    conn: Connection, migrations_dir: Path = MIGRATIONS_DIR
) -> list[str]:
    """Apply each unrecorded SQL migration in its own transaction."""
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise psycopg.ProgrammingError("迁移需要空闲连接。")

    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.schema_migrations "
            "(filename TEXT PRIMARY KEY, "
            "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {
            row[0]
            for row in conn.execute(
                "SELECT filename FROM public.schema_migrations"
            ).fetchall()
        }
    newly_applied: list[str] = []
    for migration_path in sorted(migrations_dir.glob("*.sql")):
        if migration_path.name in applied:
            continue
        with conn.transaction():
            conn.execute(migration_path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO public.schema_migrations (filename) VALUES (%s)",
                (migration_path.name,),
            )
        newly_applied.append(migration_path.name)
    return newly_applied


def main() -> int:
    database_url = get_settings().database_url
    if database_url is None:
        print("未设置 DATABASE_URL，无法运行数据库迁移。", file=sys.stderr)
        return 1

    try:
        with psycopg.connect(database_url.get_secret_value()) as conn:
            for filename in apply_migrations(conn):
                print(f"已应用迁移：{filename}")
    except psycopg.Error:
        print("数据库迁移失败（数据库错误）。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
