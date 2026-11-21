import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parents[1]
ENTRYPOINT = BACKEND_DIR / "docker-entrypoint.sh"
DOCKERFILE = (BACKEND_DIR / "Dockerfile").read_text()
MIGRATE = "run --no-sync python -m app.db.migrate"
SEED = "run --no-sync python -m app.rules.seed"


def _fake_uv(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$UV_LOG"\n'
        'if [ -n "${FAIL_MODULE:-}" ]; then\n'
        '  case "$*" in *"$FAIL_MODULE"*) exit 17 ;; esac\n'
        "fi\n"
    )
    uv.chmod(0o755)
    return bin_dir, tmp_path / "uv.log"


def _env(tmp_path: Path, fail_module: str = "") -> dict[str, str]:
    bin_dir, log = _fake_uv(tmp_path)
    return {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "UV_LOG": str(log),
        "FAIL_MODULE": fail_module,
    }


def _logged_calls(env: dict[str, str]) -> list[str]:
    log = Path(env["UV_LOG"])
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize(
    ("failed_module", "expected_calls"),
    [
        ("app.db.migrate", [MIGRATE]),
        ("app.rules.seed", [MIGRATE, SEED]),
    ],
)
def test_failure_stops_before_seed_or_api(
    tmp_path: Path, failed_module: str, expected_calls: list[str]
) -> None:
    env = _env(tmp_path, failed_module)
    marker = tmp_path / "api-started"

    result = subprocess.run(
        [str(ENTRYPOINT), "sh", "-c", f"touch {marker}"],
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert _logged_calls(env) == expected_calls
    assert not marker.exists()


def test_success_runs_in_order_execs_command_and_preserves_arguments(
    tmp_path: Path,
) -> None:
    env = _env(tmp_path)
    result_path = tmp_path / "command.json"
    env["TARGET_RESULT"] = str(result_path)
    command = (
        "import json, os, sys; "
        "open(os.environ['UV_LOG'], 'a').write('api-start\\n'); "
        "open(os.environ['TARGET_RESULT'], 'w').write(json.dumps("
        "{'pid': os.getpid(), 'args': sys.argv[1:]}))"
    )
    process = subprocess.Popen(
        [
            str(ENTRYPOINT),
            sys.executable,
            "-c",
            command,
            "alpha",
            "two words",
        ],
        env=env,
    )
    shell_pid = process.pid
    return_code = process.wait(timeout=10)
    result = json.loads(result_path.read_text())

    assert return_code == 0
    assert _logged_calls(env) == [MIGRATE, SEED, "api-start"]
    assert result == {"pid": shell_pid, "args": ["alpha", "two words"]}


def test_dockerfile_uses_executable_entrypoint_and_keeps_exec_cmd() -> None:
    assert "COPY --chmod=755 docker-entrypoint.sh ./docker-entrypoint.sh" in DOCKERFILE
    assert 'ENTRYPOINT ["./docker-entrypoint.sh"]' in DOCKERFILE
    cmd = json.loads(
        next(
            line.removeprefix("CMD ")
            for line in DOCKERFILE.splitlines()
            if line.startswith("CMD ")
        )
    )
    assert cmd == [
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    assert 'ENV PATH="/app/.venv/bin:$PATH"' in DOCKERFILE
    assert not re.search(
        r"^(?:ENV|ARG)\s+.*(?:SECRET|KEY|PASSWORD|TOKEN)|postgres(?:ql)?://",
        DOCKERFILE,
        re.MULTILINE | re.IGNORECASE,
    )


def test_deployment_docs_explain_automatic_bootstrap_and_render_commands() -> None:
    readme = (BACKEND_DIR / "README.md").read_text()
    manual = (
        BACKEND_DIR.parent / "docs/environments/render-test-backend.md"
    ).read_text()

    assert "镜像不会在启动时自动迁移数据库" not in readme
    assert "每次启动会先依次运行数据库迁移与 CMI 默认模板 seed" in readme
    assert "Docker Command 留空" in readme
    assert "Docker Command 留空" in manual
    assert "幂等 seed" in manual
    assert "Free 方案不提供 Pre-Deploy" in manual


def test_render_manual_documents_automatic_bootstrap_without_manual_commands() -> None:
    manual = (
        BACKEND_DIR.parent / "docs/environments/render-test-backend.md"
    ).read_text()

    assert "容器入口自动依次运行数据库迁移与幂等 seed" in manual
    assert "不得手工重复运行数据库迁移或 seed" in manual
    assert "uv run --no-sync python -m app.db.migrate" not in manual
    assert "uv run --no-sync python -m app.rules.seed" not in manual
