from typing import Literal, cast
from uuid import UUID

from psycopg import Connection
from pydantic import BaseModel, ConfigDict

from app.batches.pipeline import (
    ExtractionCounts,
    build_drafts,
    extract_pending,
    load_documents,
)
from app.batches.service import BatchNotFound
from app.batches.sync import sync_transactions, update_batch_summary
from app.extraction.models import DocumentExtractorProtocol
from app.rules.dsl import RuleTemplateDSL, validate_rules
from app.storage import ObjectStorage

_ERROR = "批次处理失败。"
_OWNED_BATCH = "SELECT 1 FROM public.reimbursement_batches WHERE id=%s AND user_id=%s"
_START_JOB = "INSERT INTO public.jobs (user_id,batch_id,job_type,status) VALUES (%s,%s,'process_batch','running') RETURNING id"  # noqa: E501
_LOCK_BATCH = "SELECT template_id FROM public.reimbursement_batches WHERE id=%s AND user_id=%s FOR UPDATE"  # noqa: E501
_FAIL_JOB = "UPDATE public.jobs SET status='failed',error_message=%s,updated_at=now() WHERE id=%s"  # noqa: E501


class ProcessOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: Literal["succeeded"]
    processed_documents: int
    failed_documents: int
    deferred_documents: int
    transactions: int
    missing_alerts: int
    incomplete_document_ids: list[UUID]


def _start_job(conn: Connection, user_id: UUID, batch_id: UUID) -> UUID:
    if conn.execute(_OWNED_BATCH, (batch_id, user_id)).fetchone() is None:
        raise BatchNotFound
    row = conn.execute(_START_JOB, (user_id, batch_id)).fetchone()
    if row is None:  # pragma: no cover - INSERT RETURNING always yields a row here.
        raise RuntimeError(_ERROR)
    return cast(UUID, row[0])


def _load_template(conn: Connection, template_id: UUID) -> RuleTemplateDSL:
    rules = conn.execute(
        "SELECT rules_json FROM public.rule_templates WHERE id=%s", (template_id,)
    ).fetchone()
    if rules is None:  # pragma: no cover
        raise RuntimeError(_ERROR)
    return validate_rules(rules[0])


def _run_pipeline(
    conn: Connection,
    user_id: UUID,
    batch_id: UUID,
    job_id: UUID,
    template: RuleTemplateDSL,
    counts: ExtractionCounts,
) -> ProcessOut:
    docs = load_documents(conn, batch_id)
    result = build_drafts(docs, template)
    sync_transactions(conn, user_id, batch_id, result.result.drafts)
    update_batch_summary(conn, batch_id, result.partition, result.alerts, result.result)
    count = conn.execute(
        "SELECT count(*) FROM public.transactions WHERE batch_id=%s", (batch_id,)
    ).fetchone()
    if count is None:  # pragma: no cover
        raise RuntimeError(_ERROR)
    conn.execute(
        "UPDATE public.jobs SET status='succeeded',progress=100,updated_at=now() WHERE id=%s",  # noqa: E501
        (job_id,),
    )
    return ProcessOut(
        job_id=job_id,
        status="succeeded",
        processed_documents=counts.processed,
        failed_documents=counts.failed,
        deferred_documents=counts.deferred,
        transactions=count[0],
        missing_alerts=len(result.alerts),
        incomplete_document_ids=list(map(UUID, result.result.incomplete_doc_ids)),
    )


def process_batch(
    conn: Connection,
    storage: ObjectStorage,
    extractor: DocumentExtractorProtocol,
    user_id: UUID,
    batch_id: UUID,
) -> ProcessOut:
    job_id = _start_job(conn, user_id, batch_id)
    try:
        with conn.transaction():
            batch = conn.execute(_LOCK_BATCH, (batch_id, user_id)).fetchone()
            if batch is None:  # pragma: no cover
                raise BatchNotFound
            template = _load_template(conn, batch[0])
            counts = extract_pending(conn, storage, extractor, batch_id)
            return _run_pipeline(conn, user_id, batch_id, job_id, template, counts)
    except Exception:
        conn.execute(_FAIL_JOB, (_ERROR, job_id))
        raise
