from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import Connection

from app.api.deps import get_conn, get_current_user_id, get_storage
from app.batches import documents
from app.batches.service import BatchNotFound
from app.storage import ObjectStorage, StorageError, StorageNotConfigured

router = APIRouter(prefix="/batches", tags=["documents"])


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, BatchNotFound):
        return HTTPException(status_code=404, detail="批次不存在")
    if isinstance(error, documents.DocumentNotUploaded):
        return HTTPException(status_code=409, detail="对象尚未上传")
    if isinstance(error, StorageNotConfigured):
        return HTTPException(status_code=503, detail="存储未配置")
    return HTTPException(status_code=502, detail="存储服务异常")


@router.post("/{batch_id}/documents/presign", response_model=documents.PresignResponse)
def post_presign(
    batch_id: UUID,
    body: documents.PresignRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> documents.PresignResponse:
    try:
        return documents.presign_document(conn, storage, user_id, batch_id, body)
    except (BatchNotFound, StorageNotConfigured, StorageError) as error:
        raise _http_error(error) from error


@router.post(
    "/{batch_id}/documents/confirm",
    response_model=documents.DocumentOut,
    status_code=201,
    responses={200: {"model": documents.DocumentOut}},
)
def post_confirm(
    batch_id: UUID,
    body: documents.ConfirmRequest,
    response: Response,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> documents.DocumentOut:
    try:
        document, created = documents.confirm_document(
            conn, storage, user_id, batch_id, body
        )
    except (
        BatchNotFound,
        documents.DocumentNotUploaded,
        StorageNotConfigured,
        StorageError,
    ) as error:
        raise _http_error(error) from error
    response.status_code = 201 if created else 200
    return document
