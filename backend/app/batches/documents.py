from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.batches.service import BatchNotFound
from app.storage import ObjectStorage

_Filename = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
_MimeType = Literal["image/jpeg", "image/png", "image/heic", "application/pdf"]
_Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
    "application/pdf": "pdf",
}


class _DocumentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PresignRequest(_DocumentModel):
    filename: _Filename
    mime_type: _MimeType
    sha256_hash: _Hash


class PresignResponse(_DocumentModel):
    already_exists: bool
    document_id: UUID
    upload_url: str | None
    upload_method: Literal["PUT"] | None
    expires_in: int | None


class ConfirmRequest(PresignRequest):
    document_id: UUID
    client_ocr_json: dict[str, Any] | list[Any] | None = None
    exif_taken_at: datetime | None = None


class DocumentOut(_DocumentModel):
    id: UUID
    batch_id: UUID
    original_filename: str
    mime_type: str
    file_size_bytes: int
    sha256_hash: str
    doc_role: str
    ocr_status: str
    created_at: datetime


class DocumentNotUploaded(Exception):
    pass


_DOCUMENT_COLUMNS = (
    "id, batch_id, original_filename, mime_type, file_size_bytes, "
    "sha256_hash, doc_role, ocr_status, created_at"
)


def _ensure_batch_owner(conn: Connection, user_id: UUID, batch_id: UUID) -> None:
    if not conn.execute(
        "SELECT 1 FROM public.reimbursement_batches WHERE id=%s AND user_id=%s",
        (batch_id, user_id),
    ).fetchone():
        raise BatchNotFound


def _existing_document(
    conn: Connection, user_id: UUID, batch_id: UUID, digest: str
) -> tuple[object, ...] | None:
    return conn.execute(
        f"SELECT {_DOCUMENT_COLUMNS} FROM public.raw_documents "
        "WHERE batch_id=%s AND user_id=%s AND sha256_hash=%s",
        (batch_id, user_id, digest),
    ).fetchone()


def _document_out(row: tuple[object, ...]) -> DocumentOut:
    return DocumentOut.model_validate(
        dict(zip(DocumentOut.model_fields, row, strict=True))
    )


def presign_document(
    conn: Connection,
    storage: ObjectStorage,
    user_id: UUID,
    batch_id: UUID,
    body: PresignRequest,
) -> PresignResponse:
    _ensure_batch_owner(conn, user_id, batch_id)
    if existing := _existing_document(conn, user_id, batch_id, body.sha256_hash):
        return PresignResponse(
            already_exists=True,
            document_id=cast(UUID, existing[0]),
            upload_url=None,
            upload_method=None,
            expires_in=None,
        )
    document_id = uuid4()
    url, expires = storage.presign_put(
        f"{user_id}/{batch_id}/raw/{document_id}.{_EXTENSIONS[body.mime_type]}"
    )
    return PresignResponse(
        already_exists=False,
        document_id=document_id,
        upload_url=url,
        upload_method="PUT",
        expires_in=expires,
    )


def confirm_document(
    conn: Connection,
    storage: ObjectStorage,
    user_id: UUID,
    batch_id: UUID,
    body: ConfirmRequest,
) -> tuple[DocumentOut, bool]:
    _ensure_batch_owner(conn, user_id, batch_id)
    if existing := _existing_document(conn, user_id, batch_id, body.sha256_hash):
        return _document_out(existing), False
    extension = _EXTENSIONS[body.mime_type]
    key = f"{user_id}/{batch_id}/raw/{body.document_id}.{extension}"
    size = storage.head_size(key)
    if size is None:
        raise DocumentNotUploaded
    row = conn.execute(
        f"""
        INSERT INTO public.raw_documents
            (id, user_id, batch_id, original_filename, mime_type,
             file_size_bytes, sha256_hash, storage_key, client_ocr_json,
             exif_taken_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (batch_id, sha256_hash) DO NOTHING
        RETURNING {_DOCUMENT_COLUMNS}
        """,
        (
            body.document_id,
            user_id,
            batch_id,
            body.filename,
            body.mime_type,
            size,
            body.sha256_hash,
            key,
            Jsonb(body.client_ocr_json) if body.client_ocr_json is not None else None,
            body.exif_taken_at,
        ),
    ).fetchone()
    if row:
        return _document_out(row), True
    existing = _existing_document(conn, user_id, batch_id, body.sha256_hash)
    if not existing:
        raise RuntimeError("并发确认后未找到已存在的凭证。")
    return _document_out(existing), False
