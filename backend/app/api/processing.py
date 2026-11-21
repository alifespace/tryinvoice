from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection

from app.api.deps import get_conn, get_current_user_id, get_extractor, get_storage
from app.batches.processing import ProcessOut, process_batch
from app.batches.service import BatchNotFound
from app.extraction.models import DocumentExtractorProtocol
from app.storage import ObjectStorage

router = APIRouter(prefix="/batches", tags=["processing"])


@router.post(
    "/{batch_id}/process",
    response_model=ProcessOut,
    responses={
        401: {"description": "未认证"},
        404: {"description": "批次不存在"},
        500: {"description": "批次处理失败"},
    },
)
def post_process(
    batch_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    extractor: Annotated[DocumentExtractorProtocol, Depends(get_extractor)],
) -> ProcessOut:
    try:
        return process_batch(conn, storage, extractor, user_id, batch_id)
    except BatchNotFound as error:
        raise HTTPException(status_code=404, detail="批次不存在") from error
    except Exception:
        raise HTTPException(status_code=500, detail="批次处理失败") from None
