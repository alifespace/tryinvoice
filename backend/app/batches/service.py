from datetime import date, datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from psycopg import Connection
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.storage import ObjectStorage

_BatchTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class _BatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BatchCreate(_BatchModel):
    template_id: UUID
    expense_type: Literal["business_trip", "meal"]
    title: _BatchTitle
    batch_date: date


class BatchOut(_BatchModel):
    id: UUID
    template_id: UUID
    expense_type: str
    title: str
    batch_date: date
    trip_start_date: date | None
    trip_end_date: date | None
    destinations: list[Any]
    status: str
    missing_alerts: list[Any]
    created_at: datetime
    updated_at: datetime


class BatchPage(_BatchModel):
    items: list[BatchOut]
    total: int
    limit: int
    offset: int


class BatchTemplateNotFound(Exception):
    pass


class BatchNotFound(Exception):
    pass


_BATCH_COLUMNS = """
    id, template_id, expense_type, title, batch_date, trip_start_date,
    trip_end_date, destinations, status, missing_alerts, created_at, updated_at
"""


def _row_to_out(row: tuple[object, ...]) -> BatchOut:
    (
        batch_id,
        template_id,
        expense_type,
        title,
        batch_date,
        trip_start_date,
        trip_end_date,
        destinations,
        status,
        missing_alerts,
        created_at,
        updated_at,
    ) = row
    return BatchOut.model_validate(
        {
            "id": batch_id,
            "template_id": template_id,
            "expense_type": expense_type,
            "title": title,
            "batch_date": batch_date,
            "trip_start_date": trip_start_date,
            "trip_end_date": trip_end_date,
            "destinations": cast(list[Any], destinations),
            "status": status,
            "missing_alerts": cast(list[Any], missing_alerts),
            "created_at": created_at,
            "updated_at": updated_at,
        }
    )


def create_batch(conn: Connection, user_id: UUID, body: BatchCreate) -> BatchOut:
    with conn.transaction():
        row = conn.execute(
            f"""
            INSERT INTO public.reimbursement_batches
                (user_id, template_id, expense_type, title, batch_date)
            SELECT %s, t.id, %s, %s, %s
            FROM public.rule_templates AS t
            WHERE t.id = %s AND (t.user_id IS NULL OR t.user_id = %s)
            RETURNING {_BATCH_COLUMNS}
            """,
            (
                user_id,
                body.expense_type,
                body.title,
                body.batch_date,
                body.template_id,
                user_id,
            ),
        ).fetchone()
    if row is None:
        raise BatchTemplateNotFound
    return _row_to_out(row)


def list_batches(conn: Connection, user_id: UUID, limit: int, offset: int) -> BatchPage:
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        total = conn.execute(
            """
            SELECT count(*) FROM public.reimbursement_batches
            WHERE user_id = %s
            """,
            (user_id,),
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT {_BATCH_COLUMNS}
            FROM public.reimbursement_batches
            WHERE user_id = %s
            ORDER BY batch_date DESC, created_at DESC, id
            LIMIT %s OFFSET %s
            """,
            (user_id, limit, offset),
        ).fetchall()
    if total is None:
        raise RuntimeError("批次计数查询未返回结果。")
    return BatchPage(
        items=[_row_to_out(row) for row in rows],
        total=cast(int, total[0]),
        limit=limit,
        offset=offset,
    )


def delete_batch(
    conn: Connection, user_id: UUID, batch_id: UUID, storage: ObjectStorage
) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM public.reimbursement_batches WHERE id=%s AND user_id=%s",
            (batch_id, user_id),
        ).fetchone()
        is None
    ):
        raise BatchNotFound
    keys = conn.execute(
        "SELECT storage_key FROM public.raw_documents WHERE batch_id=%s AND user_id=%s",
        (batch_id, user_id),
    ).fetchall()
    owned_prefix = f"{user_id}/{batch_id}/raw/"
    for (key,) in keys:
        # Ignore legacy placeholder values outside the server-generated namespace.
        if key.startswith(owned_prefix):
            storage.delete(key)
    with conn.transaction():
        result = conn.execute(
            """
            DELETE FROM public.reimbursement_batches
            WHERE id = %s AND user_id = %s
            """,
            (batch_id, user_id),
        )
    if result.rowcount == 0:
        raise BatchNotFound
