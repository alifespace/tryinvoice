from uuid import UUID

from psycopg import Connection

from app.storage import ObjectStorage


def delete_account(conn: Connection, user_id: UUID, storage: ObjectStorage) -> None:
    for (key,) in conn.execute(
        "SELECT storage_key FROM public.raw_documents WHERE user_id=%s", (user_id,)
    ).fetchall():
        if key.startswith(f"{user_id}/"):
            storage.delete(key)

    with conn.transaction():
        for table in ("reimbursement_batches", "rule_templates", "user_profiles"):
            conn.execute(f"DELETE FROM public.{table} WHERE user_id=%s", (user_id,))
