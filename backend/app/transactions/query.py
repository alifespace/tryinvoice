from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from itertools import groupby
from operator import attrgetter
from typing import Any, Literal
from uuid import UUID

from psycopg import Connection
from pydantic import BaseModel, ConfigDict

from app.batches.service import BatchNotFound

_SORT_KEY = attrgetter(
    "category", "region_scope", "region_detail", "tx_date", "merchant", "id"
)  # noqa: E501
_BATCH_SQL = "SELECT status,missing_alerts FROM public.reimbursement_batches WHERE id=%s AND user_id=%s"  # noqa: E501


class TransactionDocumentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    role: Literal["invoice", "receipt"]
    original_filename: str


class TransactionOut(BaseModel):
    id: UUID
    category: str
    merchant: str
    tx_date: date
    region_scope: str
    region_detail: str
    currency: str
    original_amount: Decimal
    claim_amount: Decimal
    tip_amount: Decimal
    split_count: int
    payment_status: str
    airport_direction: str | None
    airport_iata: str | None
    flags: list[str]
    notes: str
    is_user_verified: bool
    documents: list[TransactionDocumentOut]


class RegionGroup(BaseModel):
    region_detail: str
    transactions: list[TransactionOut]


class ScopeGroup(BaseModel):
    region_scope: str
    regions: list[RegionGroup]


class CategoryGroup(BaseModel):
    category: str
    scopes: list[ScopeGroup]


class TransactionListOut(BaseModel):
    batch_id: UUID
    status: str
    missing_alerts: list[Any]
    groups: list[CategoryGroup]


def group_transactions(items: Sequence[TransactionOut]) -> list[CategoryGroup]:
    return [
        CategoryGroup(
            category=category,
            scopes=[
                ScopeGroup(
                    region_scope=scope,
                    regions=[
                        RegionGroup(region_detail=detail, transactions=list(rows))
                        for detail, rows in groupby(
                            scoped, key=lambda item: item.region_detail
                        )
                    ],
                )
                for scope, scoped in groupby(
                    category_rows, key=lambda item: item.region_scope
                )
            ],
        )
        for category, category_rows in groupby(
            sorted(items, key=_SORT_KEY), key=lambda item: item.category
        )
    ]


def fetch_transactions(
    conn: Connection, user_id: UUID, batch_id: UUID, transaction_id: UUID | None = None
) -> list[TransactionOut]:
    fields = tuple(TransactionOut.model_fields)[:-1]
    filter_sql = " AND t.id=%s" if transaction_id is not None else ""
    params = (user_id, user_id, batch_id, user_id) + (
        (transaction_id,) if transaction_id is not None else ()
    )
    rows = conn.execute(
        f"SELECT {','.join(f't.{field}' for field in fields)},"
        "COALESCE(jsonb_agg(jsonb_build_object('id',d.id,'role',td.link_role,"
        "'original_filename',d.original_filename) ORDER BY CASE td.link_role "
        "WHEN 'invoice' THEN 0 ELSE 1 END,d.id) FILTER (WHERE d.id IS NOT NULL),"
        "'[]'::jsonb) FROM public.transactions t "
        "LEFT JOIN public.transaction_documents td ON td.transaction_id=t.id "
        "AND td.user_id=%s AND td.link_role IN ('invoice','receipt') "
        "LEFT JOIN public.raw_documents d ON d.id=td.raw_document_id "
        "AND d.user_id=%s "
        f"WHERE t.batch_id=%s AND t.user_id=%s{filter_sql} GROUP BY t.id",
        params,
    ).fetchall()
    return [
        TransactionOut(**dict(zip(fields, row[:-1], strict=True)), documents=row[-1])
        for row in rows
    ]


def list_transactions(
    conn: Connection, user_id: UUID, batch_id: UUID
) -> TransactionListOut:
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        batch = conn.execute(_BATCH_SQL, (batch_id, user_id)).fetchone()
        if batch is None:
            raise BatchNotFound
        items = fetch_transactions(conn, user_id, batch_id)
    return TransactionListOut(
        batch_id=batch_id,
        status=batch[0],
        missing_alerts=batch[1],
        groups=group_transactions(items),
    )
