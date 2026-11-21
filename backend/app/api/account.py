from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import Connection

from app.account import delete_account
from app.api.deps import get_conn, get_current_user_id, get_storage
from app.storage import ObjectStorage, StorageError, StorageNotConfigured

router = APIRouter(tags=["account"])


@router.delete(
    "/account", status_code=204, response_class=Response, response_model=None
)
def remove_account(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> Response:
    try:
        delete_account(conn, user_id, storage)
    except (StorageNotConfigured, StorageError) as error:
        status = 503 if isinstance(error, StorageNotConfigured) else 502
        detail = "存储未配置" if status == 503 else "存储服务异常"
        raise HTTPException(status_code=status, detail=detail) from error
    return Response(status_code=204)
