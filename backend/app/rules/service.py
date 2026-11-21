from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, cast
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.rules.dsl import RuleTemplateDSL, validate_rules

_TemplateName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]


class _TemplateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TemplateOut(_TemplateModel):
    id: UUID
    name: str
    description: str
    schema_version: str
    is_system_default: bool
    rules: RuleTemplateDSL
    created_at: datetime
    updated_at: datetime


class TemplateCreate(_TemplateModel):
    name: _TemplateName
    description: str = Field(default="", max_length=1000)
    rules: RuleTemplateDSL


class TemplateClone(_TemplateModel):
    name: _TemplateName | None = None


class TemplatePatch(_TemplateModel):
    name: _TemplateName | None = None
    description: str | None = Field(default=None, max_length=1000)
    rules: RuleTemplateDSL | None = None

    @model_validator(mode="after")
    def at_least_one_field_is_set(self) -> TemplatePatch:
        if self.name is None and self.description is None and self.rules is None:
            raise ValueError("至少需要提供一个要更新的字段。")
        return self


class TemplateNotFound(Exception):
    pass


class SystemTemplateReadOnly(Exception):
    pass


def _row_to_out(row: tuple[object, ...]) -> TemplateOut:
    (
        template_id,
        name,
        description,
        schema_version,
        is_system_default,
        rules_json,
        created_at,
        updated_at,
    ) = row
    rules = validate_rules(cast(Mapping[str, object], rules_json))
    return TemplateOut.model_validate(
        {
            "id": template_id,
            "name": name,
            "description": description,
            "schema_version": schema_version,
            "is_system_default": is_system_default,
            "rules": rules,
            "created_at": created_at,
            "updated_at": updated_at,
        }
    )


_OUTPUT_COLUMNS = """
    id, name, description, schema_version, is_system_default, rules_json,
    created_at, updated_at
"""


def list_templates(conn: Connection, user_id: UUID) -> list[TemplateOut]:
    rows = conn.execute(
        f"""
        SELECT {_OUTPUT_COLUMNS}
        FROM public.rule_templates
        WHERE user_id IS NULL OR user_id = %s
        ORDER BY is_system_default DESC, created_at, id
        """,
        (user_id,),
    ).fetchall()
    return [_row_to_out(row) for row in rows]


def create_template(
    conn: Connection, user_id: UUID, body: TemplateCreate
) -> TemplateOut:
    with conn.transaction():
        row = conn.execute(
            f"""
            INSERT INTO public.rule_templates
                (user_id, is_system_default, name, description, schema_version,
                 rules_json)
            VALUES (%s, false, %s, %s, '1.0', %s)
            RETURNING {_OUTPUT_COLUMNS}
            """,
            (
                user_id,
                body.name,
                body.description,
                Jsonb(body.rules.model_dump(mode="json")),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("创建模板后未返回记录。")
    return _row_to_out(row)


def clone_template(
    conn: Connection, user_id: UUID, template_id: UUID, body: TemplateClone
) -> TemplateOut:
    with conn.transaction():
        source_row = conn.execute(
            f"""
            SELECT {_OUTPUT_COLUMNS}
            FROM public.rule_templates
            WHERE id = %s AND (user_id IS NULL OR user_id = %s)
            """,
            (template_id, user_id),
        ).fetchone()
        if source_row is None:
            raise TemplateNotFound

        source = _row_to_out(source_row)
        rules = source.rules.model_dump(mode="json")
        rules["template_code"] = f"custom_{uuid4().hex[:16]}"
        name = body.name or f"{source.name}（副本）"[:120]
        row = conn.execute(
            f"""
            INSERT INTO public.rule_templates
                (user_id, is_system_default, name, description, schema_version,
                 rules_json)
            VALUES (%s, false, %s, %s, %s, %s)
            RETURNING {_OUTPUT_COLUMNS}
            """,
            (
                user_id,
                name,
                source.description,
                source.schema_version,
                Jsonb(rules),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("克隆模板后未返回记录。")
    return _row_to_out(row)


def update_template(
    conn: Connection, user_id: UUID, template_id: UUID, body: TemplatePatch
) -> TemplateOut:
    with conn.transaction():
        existing = conn.execute(
            """
            SELECT user_id, is_system_default
            FROM public.rule_templates
            WHERE id = %s AND (user_id IS NULL OR user_id = %s)
            """,
            (template_id, user_id),
        ).fetchone()
        if existing is None:
            raise TemplateNotFound
        if existing[0] is None or existing[1]:
            raise SystemTemplateReadOnly

        row = conn.execute(
            f"""
            UPDATE public.rule_templates
            SET name = COALESCE(%s, name),
                description = COALESCE(%s, description),
                rules_json = COALESCE(%s, rules_json),
                updated_at = now()
            WHERE id = %s AND user_id = %s AND NOT is_system_default
            RETURNING {_OUTPUT_COLUMNS}
            """,
            (
                body.name,
                body.description,
                Jsonb(body.rules.model_dump(mode="json")) if body.rules else None,
                template_id,
                user_id,
            ),
        ).fetchone()
    if row is None:
        raise TemplateNotFound
    return _row_to_out(row)
