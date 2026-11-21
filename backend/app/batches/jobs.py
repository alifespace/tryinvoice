from typing import cast
from uuid import UUID

from psycopg import Connection

STALE_SECONDS = 1800


class JobConflict(Exception):
    pass


def start_job(
    conn: Connection,
    user_id: UUID,
    batch_id: UUID,
    job_type: str,
    *,
    stale_seconds: int = STALE_SECONDS,
) -> UUID:
    conflict = False
    job_id: UUID | None = None
    with conn.transaction():
        lock_key = f"{batch_id}:{job_type}"
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock_key,)
        )
        conn.execute(
            "UPDATE public.jobs SET status='failed',error_message='任务超时。',"
            "updated_at=now() WHERE batch_id=%s AND user_id=%s AND job_type=%s "
            "AND status='running' "
            "AND updated_at < now() - make_interval(secs => %s)",
            (batch_id, user_id, job_type, stale_seconds),
        )
        running = conn.execute(
            "SELECT 1 FROM public.jobs WHERE batch_id=%s AND user_id=%s "
            "AND job_type=%s AND status='running' LIMIT 1",
            (batch_id, user_id, job_type),
        ).fetchone()
        if running is not None:
            conflict = True
        else:
            row = conn.execute(
                "INSERT INTO public.jobs (user_id,batch_id,job_type,status) "
                "VALUES (%s,%s,%s,'running') RETURNING id",
                (user_id, batch_id, job_type),
            ).fetchone()
            if row is None:  # pragma: no cover
                raise RuntimeError("无法创建任务")
            job_id = cast(UUID, row[0])

    if conflict:
        raise JobConflict
    if job_id is None:  # pragma: no cover
        raise RuntimeError("无法创建任务")
    return job_id
