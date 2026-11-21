from typing import Literal, NamedTuple
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.extraction.models import (
    DocumentExtractorProtocol,
    ExtractedDocument,
    ExtractionInput,
)
from app.rules.airport import MissingAlert, check_airport_transfers
from app.rules.documents import PartitionResult, RuleDocument, partition_documents
from app.rules.dsl import RuleTemplateDSL
from app.rules.pairing import pair_documents
from app.rules.transactions import TransactionBuildResult, build_transactions
from app.storage import ObjectStorage, StorageError, StorageNotConfigured

_MARK_FAILED = "UPDATE public.raw_documents SET ocr_status='failed',doc_role='unknown' WHERE id=%s AND batch_id=%s"  # noqa: E501
_MARK_COMPLETED = "UPDATE public.raw_documents SET doc_role=%s,extracted_json=%s,quality_score=%s,ocr_status='completed' WHERE id=%s AND batch_id=%s"  # noqa: E501
_PENDING_DOCUMENTS = "SELECT id,mime_type,storage_key,client_ocr_json FROM public.raw_documents WHERE batch_id=%s AND ocr_status='pending' ORDER BY created_at,id"  # noqa: E501
_COMPLETED_DOCUMENTS = "SELECT id,extracted_json,quality_score FROM public.raw_documents WHERE batch_id=%s AND ocr_status='completed' AND extracted_json IS NOT NULL ORDER BY created_at,id"  # noqa: E501


class ExtractionCounts(NamedTuple):
    processed: int
    failed: int
    deferred: int


class PipelineResult(NamedTuple):
    partition: PartitionResult
    alerts: tuple[MissingAlert, ...]
    result: TransactionBuildResult


_PendingRow = tuple[UUID, str, str, dict[str, object] | list[object] | None]


def _mark_failed(conn: Connection, doc_id: UUID, batch_id: UUID) -> None:
    conn.execute(_MARK_FAILED, (doc_id, batch_id))


def _mark_completed(
    conn: Connection,
    doc_id: UUID,
    batch_id: UUID,
    extracted: ExtractedDocument,
) -> None:
    conn.execute(
        _MARK_COMPLETED,
        (
            extracted.doc_role,
            Jsonb(extracted.model_dump(mode="json")),
            extracted.confidence,
            doc_id,
            batch_id,
        ),
    )


def _extract_one(
    conn: Connection,
    storage: ObjectStorage,
    extractor: DocumentExtractorProtocol,
    batch_id: UUID,
    row: _PendingRow,
) -> Literal["processed", "failed", "deferred"]:
    doc_id, mime, key, ocr = row
    try:
        content = storage.get(key) if mime == "application/pdf" else None
    except StorageError, StorageNotConfigured:
        return "deferred"
    try:
        extracted = extractor.extract(
            ExtractionInput(mime_type=mime, content=content, client_ocr_json=ocr)
        )
    except Exception:
        extracted = None
    if extracted is None:
        _mark_failed(conn, doc_id, batch_id)
        return "failed"
    _mark_completed(conn, doc_id, batch_id, extracted)
    return "processed"


def extract_pending(
    conn: Connection,
    storage: ObjectStorage,
    extractor: DocumentExtractorProtocol,
    batch_id: UUID,
) -> ExtractionCounts:
    rows = conn.execute(_PENDING_DOCUMENTS, (batch_id,)).fetchall()
    counts = {"processed": 0, "failed": 0, "deferred": 0}
    for row in rows:
        status = _extract_one(conn, storage, extractor, batch_id, row)
        counts[status] += 1
    return ExtractionCounts(**counts)


def load_documents(conn: Connection, batch_id: UUID) -> tuple[RuleDocument, ...]:
    rows = conn.execute(_COMPLETED_DOCUMENTS, (batch_id,)).fetchall()
    return tuple(
        RuleDocument(str(i), ExtractedDocument.model_validate(d), s) for i, d, s in rows
    )  # noqa: E501


def build_drafts(
    docs: tuple[RuleDocument, ...], template: RuleTemplateDSL
) -> PipelineResult:
    rules = template.compliance_rules
    partition = partition_documents(docs, rules)
    built = build_transactions(pair_documents(partition, rules), template)
    checked = check_airport_transfers(built.drafts, partition.trip_applications, rules)
    result = TransactionBuildResult(checked.drafts, built.incomplete_doc_ids)
    return PipelineResult(partition, checked.alerts, result)
