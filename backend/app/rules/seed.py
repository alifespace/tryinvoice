import json
import sys
from pathlib import Path
from typing import Literal

import psycopg
from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.config import get_settings
from app.rules.dsl import RuleTemplateDSL, validate_rules

_DEFAULT_TEMPLATE_CODE = "cmi_default_v1"
_DEFAULT_TEMPLATE_NAME = "CMI 报销规则"
_DEFAULT_TEMPLATE_DESCRIPTION = "CMI 报销规则的系统默认模板。"
_ADVISORY_LOCK_KEY = 6_773_921_204
_TEMPLATE_PATH = Path(__file__).with_name("cmi_default_v1.json")


def load_cmi_default() -> RuleTemplateDSL:
    raw_data = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return validate_rules(json.loads(raw_data))


def seed_cmi_default(
    conn: Connection,
) -> Literal["inserted", "updated", "unchanged"]:
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise psycopg.ProgrammingError("默认模板种子需要空闲连接。")

    template = load_cmi_default()
    rules_json = template.model_dump(mode="json")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK_KEY,))
        row = conn.execute(
            """
            SELECT id, name, description, schema_version, rules_json
            FROM public.rule_templates
            WHERE user_id IS NULL
              AND is_system_default
              AND rules_json->>'template_code' = %s
            """,
            (_DEFAULT_TEMPLATE_CODE,),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO public.rule_templates
                    (user_id, is_system_default, name, description,
                     schema_version, rules_json)
                VALUES (NULL, true, %s, %s, '1.0', %s)
                """,
                (
                    _DEFAULT_TEMPLATE_NAME,
                    _DEFAULT_TEMPLATE_DESCRIPTION,
                    Jsonb(rules_json),
                ),
            )
            return "inserted"

        template_id, name, description, schema_version, existing_rules = row
        if (
            name != _DEFAULT_TEMPLATE_NAME
            or description != _DEFAULT_TEMPLATE_DESCRIPTION
            or schema_version != "1.0"
            or existing_rules != rules_json
        ):
            conn.execute(
                """
                UPDATE public.rule_templates
                SET name = %s, description = %s, schema_version = '1.0',
                    rules_json = %s, updated_at = now()
                WHERE id = %s
                """,
                (
                    _DEFAULT_TEMPLATE_NAME,
                    _DEFAULT_TEMPLATE_DESCRIPTION,
                    Jsonb(rules_json),
                    template_id,
                ),
            )
            return "updated"
    return "unchanged"


def main() -> int:
    database_url = get_settings().database_url
    if database_url is None:
        print("未设置 DATABASE_URL，无法写入默认规则模板。", file=sys.stderr)
        return 1

    try:
        with psycopg.connect(database_url.get_secret_value()) as conn:
            result = seed_cmi_default(conn)
    except psycopg.Error:
        print("写入默认规则模板失败（数据库错误）。", file=sys.stderr)
        return 1
    print(f"CMI 默认规则模板：{result}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
