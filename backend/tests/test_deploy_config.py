import json
import re
from pathlib import Path

from app.config import Settings

BACKEND_DIR = Path(__file__).parents[1]


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (BACKEND_DIR / ".env.example").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", maxsplit=1)
            values[key] = value
    return values


def test_env_example_matches_settings_fields() -> None:
    expected = {field.upper() for field in Settings.model_fields}
    assert set(_env_example()) == expected


def test_env_example_leaves_secret_values_empty() -> None:
    for key, value in _env_example().items():
        if re.search(r"SECRET|KEY|PASSWORD|DATABASE_URL|JWT", key):
            assert value == "", key


def test_dockerfile_uses_pinned_images_and_safe_runtime() -> None:
    dockerfile = (BACKEND_DIR / "Dockerfile").read_text()
    from_lines = re.findall(r"^FROM\s+(\S+)", dockerfile, flags=re.MULTILINE)

    assert any(re.fullmatch(r"python:3\.14-slim", image) for image in from_lines)
    assert any(
        re.fullmatch(r"ghcr\.io/astral-sh/uv:\d+\.\d+\.\d+", image)
        for image in from_lines
    )
    assert all("latest" not in image for image in from_lines)
    assert re.search(r"^USER\s+(?!root\b)\S+", dockerfile, flags=re.MULTILINE)
    assert "HEALTHCHECK" in dockerfile
    assert "/api/v1/health" in dockerfile

    cmd_line = next(
        line.removeprefix("CMD ").strip()
        for line in dockerfile.splitlines()
        if line.startswith("CMD ")
    )
    assert json.loads(cmd_line) == [
        "uv",
        "run",
        "--no-sync",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    assert not re.search(
        r"^COPY\s+(?:--\S+\s+)?\.env(?:\s|$)", dockerfile, re.MULTILINE
    )
    assert not re.search(r"^COPY\s+(?:--\S+\s+)?\.(?:\s|$)", dockerfile, re.MULTILINE)
    assert not re.search(
        r"^(?:ENV|ARG)\s+.*(?:SECRET|KEY|PASSWORD|TOKEN)",
        dockerfile,
        flags=re.MULTILINE | re.IGNORECASE,
    )


def test_dockerignore_excludes_local_and_test_files() -> None:
    dockerignore = (BACKEND_DIR / ".dockerignore").read_text().splitlines()
    assert ".env" in dockerignore
    assert "tests" in dockerignore
    assert ".venv" in dockerignore


def test_readme_documents_container_build_and_manual_migration() -> None:
    readme = (BACKEND_DIR / "README.md").read_text()
    assert "docker build -t tryinvoice-backend backend/" in readme
    assert "python -m app.db.migrate" in readme


def test_readme_documents_local_dev_user_id_env_file_rules() -> None:
    readme = (BACKEND_DIR / "README.md").read_text()
    env_example = (BACKEND_DIR / ".env.example").read_text()

    assert "必须删除或注释 `LOCAL_DEV_USER_ID=`" in readme
    assert "不能将它留空" in readme
    assert "`APP_ENV=production`" in readme
    assert "填为有效 UUID" in readme
    assert "未启用或生产环境删除/注释此项，不得留空" in env_example
