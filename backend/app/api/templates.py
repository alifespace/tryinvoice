from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from psycopg import Connection

from app.api.deps import get_conn, get_current_user_id
from app.rules.service import (
    SystemTemplateReadOnly,
    TemplateClone,
    TemplateCreate,
    TemplateNotFound,
    TemplateOut,
    TemplatePatch,
    clone_template,
    create_template,
    list_templates,
    update_template,
)

router = APIRouter(prefix="/templates", tags=["templates"])


def _run_service[Result](operation: Callable[..., Result], *args: object) -> Result:
    try:
        return operation(*args)
    except TemplateNotFound as error:
        raise HTTPException(status_code=404, detail="模板不存在") from error
    except SystemTemplateReadOnly as error:
        raise HTTPException(status_code=403, detail="系统默认模板不可修改") from error


@router.get("", response_model=list[TemplateOut])
def get_templates(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
) -> list[TemplateOut]:
    return _run_service(list_templates, conn, user_id)


@router.post("", response_model=TemplateOut, status_code=201)
def post_template(
    body: TemplateCreate,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
) -> TemplateOut:
    return _run_service(create_template, conn, user_id, body)


@router.post("/{template_id}/clone", response_model=TemplateOut, status_code=201)
def post_template_clone(
    template_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
    body: Annotated[TemplateClone | None, Body()] = None,
) -> TemplateOut:
    return _run_service(
        clone_template, conn, user_id, template_id, body or TemplateClone()
    )


@router.patch("/{template_id}", response_model=TemplateOut)
def patch_template(
    template_id: UUID,
    body: TemplatePatch,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    conn: Annotated[Connection, Depends(get_conn)],
) -> TemplateOut:
    return _run_service(update_template, conn, user_id, template_id, body)
