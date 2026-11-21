from collections.abc import Sequence
from typing import cast
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.rules.airport import MissingAlert
from app.rules.documents import PartitionResult
from app.rules.transactions import TransactionBuildResult, TransactionDraft

_ERROR = "批次处理失败。"
_FIELDS = (
    "category",
    "region_scope",
    "region_detail",
    "tx_date",
    "merchant",
    "currency",
    "original_amount",
    "tip_amount",
    "split_count",
    "claim_amount",
    "airport_direction",
    "airport_iata",
    "payment_status",
    "flags",
)
_UPDATE = f"UPDATE public.transactions SET {','.join(f'{field}=%s' for field in _FIELDS)},updated_at=now() WHERE id=%s"  # noqa: E501
_INSERT = (
    f"INSERT INTO public.transactions (user_id,batch_id,{','.join(_FIELDS)}) "
    f"VALUES ({','.join(['%s'] * 16)}) RETURNING id"
)


def _values(draft: TransactionDraft) -> tuple[object, ...]:
    return tuple(
        Jsonb(list(draft.flags)) if field == "flags" else getattr(draft, field)
        for field in _FIELDS
    )


def _linked_documents(conn: Connection, batch_id: UUID) -> dict[UUID, set[UUID]]:
    links: dict[UUID, set[UUID]] = {}
    for transaction_id, document_id in conn.execute(
        "SELECT d.transaction_id,d.raw_document_id FROM public.transaction_documents d "
        "JOIN public.transactions t ON t.id=d.transaction_id WHERE t.batch_id=%s",
        (batch_id,),
    ).fetchall():
        links.setdefault(transaction_id, set()).add(document_id)
    return links


def _link(
    conn: Connection,
    transaction_id: UUID,
    user_id: UUID,
    draft: TransactionDraft,
    insert: bool,
) -> None:
    query = (
        "INSERT INTO public.transaction_documents "
        "(transaction_id,raw_document_id,user_id,link_role) VALUES (%s,%s,%s,%s)"
        if insert
        else "UPDATE public.transaction_documents SET link_role=%s "
        "WHERE transaction_id=%s AND raw_document_id=%s"
    )
    for document_value, role in (
        (draft.invoice_doc_id, "invoice"),
        (draft.receipt_doc_id, "receipt"),
    ):
        if document_value is None:
            continue
        document_id = UUID(document_value)
        values = (
            (transaction_id, document_id, user_id, role)
            if insert
            else (role, transaction_id, document_id)
        )
        conn.execute(query, values)


def _sync_draft(
    conn: Connection,
    user_id: UUID,
    batch_id: UUID,
    draft: TransactionDraft,
    protected: set[UUID],
    existing: dict[frozenset[UUID], UUID],
    retained: set[UUID],
) -> None:
    documents = frozenset(
        UUID(value) for value in (draft.invoice_doc_id, draft.receipt_doc_id) if value
    )
    if documents & protected:
        return
    transaction_id = existing.get(documents)
    if transaction_id is None:
        row = conn.execute(_INSERT, (user_id, batch_id, *_values(draft))).fetchone()
        if row is None:
            raise RuntimeError(_ERROR)
        transaction_id = cast(UUID, row[0])
        _link(conn, transaction_id, user_id, draft, True)
    else:
        conn.execute(_UPDATE, (*_values(draft), transaction_id))
        _link(conn, transaction_id, user_id, draft, False)
    retained.add(transaction_id)


def sync_transactions(
    conn: Connection, user_id: UUID, batch_id: UUID, drafts: Sequence[TransactionDraft]
) -> None:
    rows = conn.execute(
        "SELECT id,is_user_verified FROM public.transactions "
        "WHERE batch_id=%s ORDER BY id",
        (batch_id,),
    ).fetchall()
    links = _linked_documents(conn, batch_id)
    protected: set[UUID] = set()
    existing: dict[frozenset[UUID], UUID] = {}
    for transaction_id, verified in rows:
        documents = links.get(transaction_id, set())
        if verified:
            protected.update(documents)
        elif documents:
            existing[frozenset(documents)] = transaction_id
    retained: set[UUID] = set()
    for draft in drafts:
        _sync_draft(conn, user_id, batch_id, draft, protected, existing, retained)
    for transaction_id, verified in rows:
        if not verified and transaction_id not in retained:
            conn.execute(
                "DELETE FROM public.transactions WHERE id=%s", (transaction_id,)
            )


def _trip_dates(partition: PartitionResult) -> tuple[object, object, list[str]]:
    trips = partition.trip_applications
    start = min(
        filter(None, (doc.extracted.trip_start_date for doc in trips)),
        default=None,
    )
    end = max(
        filter(None, (doc.extracted.trip_end_date for doc in trips)),
        default=None,
    )
    destinations = sorted(
        {airport for doc in trips for airport in doc.extracted.destination_airports}
    )
    return start, end, destinations


def _review_required(
    payload: list[dict[str, str]],
    partition: PartitionResult,
    result: TransactionBuildResult,
) -> bool:
    return bool(
        payload
        or result.incomplete_doc_ids
        or partition.unknown
        or any(
            draft.flags
            or draft.payment_status in {"missing_payment", "missing_invoice"}
            for draft in result.drafts
        )
    )


def _write_duplicate_links(
    conn: Connection, batch_id: UUID, partition: PartitionResult
) -> None:
    conn.execute(
        "UPDATE public.raw_documents SET duplicate_of_id=NULL WHERE batch_id=%s",
        (batch_id,),
    )
    for duplicate, kept in partition.duplicate_of.items():
        conn.execute(
            "UPDATE public.raw_documents SET duplicate_of_id=%s "
            "WHERE id=%s AND batch_id=%s",
            (UUID(kept), UUID(duplicate), batch_id),
        )


def update_batch_summary(
    conn: Connection,
    batch_id: UUID,
    partition: PartitionResult,
    alerts: Sequence[MissingAlert],
    result: TransactionBuildResult,
) -> None:
    starts, ends, destinations = _trip_dates(partition)
    payload = [alert.as_json() for alert in alerts]
    status = (
        "review_required" if _review_required(payload, partition, result) else "ready"
    )
    conn.execute(
        "UPDATE public.reimbursement_batches SET trip_start_date=%s,trip_end_date=%s,"
        "destinations=%s,missing_alerts=%s,status=%s,updated_at=now() WHERE id=%s",
        (starts, ends, Jsonb(destinations), Jsonb(payload), status, batch_id),
    )
    _write_duplicate_links(conn, batch_id, partition)
