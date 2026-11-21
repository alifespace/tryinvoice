from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg import Connection

from app.api.deps import get_conn, get_current_user_id, get_storage
from app.batches.service import (
    BatchCreate,
    BatchNotFound,
    BatchOut,
    BatchPage,
    BatchTemplateNotFound,
    create_batch,
    delete_batch,
    list_batches,
)
from app.storage import ObjectStorage, StorageError, StorageNotConfigured

router = APIRouter(prefix="/batches", tags=["batches"])


@router.post("", response_model=BatchOut, status_code=201)
def post_batch(
    body: BatchCreate,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
) -> BatchOut:
    try:
        return create_batch(conn, user_id, body)
    except BatchTemplateNotFound as error:
        raise HTTPException(status_code=422, detail="模板不存在或不可见") from error


@router.get("", response_model=BatchPage)
def get_batches(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BatchPage:
    return list_batches(conn, user_id, limit, offset)


@router.delete(
    "/{batch_id}", status_code=204, response_class=Response, response_model=None
)
def remove_batch(
    batch_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> Response:
    try:
        delete_batch(conn, user_id, batch_id, storage)
    except BatchNotFound as error:
        raise HTTPException(status_code=404, detail="批次不存在") from error
    except StorageNotConfigured as error:
        raise HTTPException(status_code=503, detail="存储未配置") from error
    except StorageError as error:
        raise HTTPException(status_code=502, detail="存储服务异常") from error
    return Response(status_code=204)
