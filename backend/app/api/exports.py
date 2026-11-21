from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection

from app.api.deps import get_conn, get_current_user_id, get_storage
from app.batches.jobs import JobConflict
from app.batches.service import BatchNotFound
from app.exporting import service as export_service
from app.storage import ObjectStorage

router = APIRouter(tags=["export"])
_EXPORT_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {},
    404: {},
    409: {"description": "任务冲突或批次暂无可导出交易"},
    500: {},
}


@router.post(
    "/batches/{batch_id}/export",
    response_model=export_service.ExportOut,
    responses=_EXPORT_RESPONSES,
)
def post_export(
    batch_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> export_service.ExportOut:
    try:
        return export_service.export_batch(conn, storage, user_id, batch_id)
    except BatchNotFound as error:
        raise HTTPException(status_code=404, detail="批次不存在") from error
    except export_service.ExportEmpty as error:
        raise HTTPException(status_code=409, detail="批次暂无可导出交易") from error
    except JobConflict:
        conflict = HTTPException(status_code=409, detail="任务正在执行，请稍后重试")
        raise conflict from None
    except Exception:
        raise HTTPException(status_code=500, detail="导出失败") from None


@router.get(
    "/jobs/{job_id}",
    response_model=export_service.JobOut,
    responses={401: {"description": "未认证"}, 404: {"description": "任务不存在"}},
)
def get_export_job(
    job_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> export_service.JobOut:
    try:
        return export_service.get_job(conn, storage, user_id, job_id)
    except export_service.JobNotFound as error:
        raise HTTPException(status_code=404, detail="任务不存在") from error
