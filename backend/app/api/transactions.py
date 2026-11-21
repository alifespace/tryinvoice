from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from psycopg import Connection
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.api.deps import get_conn, get_current_user_id
from app.batches.service import BatchNotFound
from app.transactions.query import TransactionListOut, TransactionOut, list_transactions
from app.transactions.review import (
    InvalidTransactionPatch,
    LinkedDocumentConflict,
    TransactionNotFound,
    TransactionPatch,
    patch_transaction,
)


class _SanitizedPatchValidationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()
        if self.path != "/transactions/{tx_id}":
            return original_handler

        async def handle(request: Request) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {
                                "loc": ["body"],
                                "msg": "交易修订无效",
                                "type": "value_error",
                            }
                        ]
                    },
                )

        return handle


router = APIRouter(tags=["transactions"], route_class=_SanitizedPatchValidationRoute)


@router.get(
    "/batches/{batch_id}/transactions",
    response_model=TransactionListOut,
    responses={401: {"description": "未认证"}, 404: {"description": "批次不存在"}},
)
def get_transactions(
    batch_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
) -> TransactionListOut:
    try:
        return list_transactions(conn, user_id, batch_id)
    except BatchNotFound as error:
        raise HTTPException(status_code=404, detail="批次不存在") from error


@router.patch(
    "/transactions/{tx_id}",
    response_model=TransactionOut,
    responses={
        401: {"description": "未认证"},
        404: {"description": "交易不存在"},
        409: {"description": "凭证已关联其他交易"},
        422: {"description": "交易修订无效"},
    },
)
def update_transaction(
    tx_id: UUID,
    body: TransactionPatch,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
) -> TransactionOut:
    try:
        return patch_transaction(conn, user_id, tx_id, body)
    except TransactionNotFound as error:
        raise HTTPException(status_code=404, detail="交易不存在") from error
    except LinkedDocumentConflict as error:
        raise HTTPException(status_code=409, detail="凭证已关联其他交易") from error
    except InvalidTransactionPatch as error:
        raise HTTPException(status_code=422, detail="交易修订无效") from error
