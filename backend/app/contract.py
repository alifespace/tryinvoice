import json
import sys
from pathlib import Path
from typing import Any

from fastapi.openapi.models import OpenAPI

from app.main import app

_PATH = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"
_REQUIRED_PATHS = {
    "/api/v1/health",
    "/api/v1/templates",
    "/api/v1/batches",
    "/api/v1/batches/{batch_id}",
    "/api/v1/batches/{batch_id}/documents/presign",
    "/api/v1/batches/{batch_id}/documents/confirm",
    "/api/v1/account",
}


def build_openapi() -> dict[str, Any]:
    return app.openapi()


def validate_openapi(schema: dict[str, Any]) -> None:
    if not schema.get("openapi", "").startswith("3.1"):
        raise ValueError("OpenAPI 3.1 is required")
    OpenAPI.model_validate(schema)
    paths = schema.get("paths", {})
    if not isinstance(paths, dict) or _REQUIRED_PATHS - paths.keys():
        raise ValueError("OpenAPI contract is missing required paths")
    components = schema.get("components", {})
    schemes = (
        components.get("securitySchemes", {}) if isinstance(components, dict) else {}
    )
    if not isinstance(schemes, dict) or "HTTPBearer" not in schemes:
        raise ValueError("OpenAPI contract is missing HTTPBearer")


def main() -> None:
    validate_openapi(build_openapi())
    content = (
        json.dumps(
            build_openapi(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    )
    if "--check" in sys.argv[1:]:
        if not _PATH.is_file() or _PATH.read_text(encoding="utf-8") != content:
            raise SystemExit(
                "OpenAPI 契约已过期，请运行 python -m app.contract 重新生成。"
            )
        print("OpenAPI 契约与当前应用一致。")
    else:
        _PATH.write_text(content, encoding="utf-8")
        print("已生成 contracts/openapi.json。")


if __name__ == "__main__":
    main()
