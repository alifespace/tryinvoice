import os
import re
import runpy
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
_SCANNER = runpy.run_path(str(ROOT / "ios/scripts/ci-scan-secrets.py"))


def _assert_secret_references(workflow: str) -> None:
    secret_line = re.compile(
        r"(?m)^[ \t]{10}(TEST_RUNNER_[A-Z0-9_]+):[ \t]*"
        r"\$\{\{[ \t]*secrets\.([A-Z0-9_]+)[ \t]*\}\}$"
    )
    expected = {
        (f"TEST_RUNNER_TRYINVOICE_SEAM_{name}", f"TRYINVOICE_TEST_{name}")
        for name in "API_BASE_URL SUPABASE_URL SUPABASE_ANON_KEY EMAIL PASSWORD".split()
    }
    remaining = secret_line.sub("", workflow).replace(
        "name: Scan diagnostics for secrets", ""
    )
    assert len(secret_line.findall(workflow)) == 10
    assert re.search(r"(?<![\w-])secrets\b", remaining, flags=re.IGNORECASE) is None
    for step in ("Build and test", "Scan diagnostics for secrets"):
        entries = secret_line.findall(_step_block(workflow, step))
        assert len(entries) == 5 and set(entries) == expected
    build_step = _step_block(workflow, "Build and test")
    scan_step = _step_block(workflow, "Scan diagnostics for secrets")
    assert all(
        check in build_step
        for check in (
            "run: bash ios/scripts/ci-build-test.sh",
            'TEST_RUNNER_TRYINVOICE_SEAM_REQUIRED: "1"',
        )
    )
    assert all(
        check in scan_step
        for check in ("if: always()", "run: python3 ios/scripts/ci-scan-secrets.py")
    )
    assert "export TRYINVOICE_SEAM_" not in build_step
    assert "id: scan_diagnostics_secrets" in workflow and (
        "steps.scan_diagnostics_secrets.outputs.scan_complete == 'true'" in workflow
    )


def _step_block(workflow: str, step_name: str) -> str:
    match = re.search(
        rf"(?ms)^      - name: {re.escape(step_name)}\n(.*?)(?=^      - |\Z)", workflow
    )
    assert match is not None
    return match.group(1)


def _assert_workflow_permissions(workflow: str) -> None:
    assert "runs-on: macos-15" in workflow
    assert "workflow_dispatch:" in workflow
    assert re.search(r"(?m)^  contents: read$", workflow)
    assert "latest" not in workflow.lower()
    assert "persist-credentials: false" in workflow
    assert "set -x" not in workflow
    assert "echo ${{" not in workflow
    assert "ACTIONS_STEP_DEBUG" not in workflow
    _assert_secret_references(workflow)


def _assert_artifact_scope(workflow: str) -> None:
    artifact_paths = re.findall(r"(?m)^\s+(ios/build/ci/\S+)$", workflow)
    assert set(artifact_paths) == {
        "ios/build/ci/*.xcresult",
        "ios/build/ci/logs/*.log",
        "ios/build/ci/screenshots/**",
    }
    assert all(path.startswith("ios/build/ci/") for path in artifact_paths)
    assert "DerivedData" not in "\n".join(artifact_paths)
    assert "if: always()" in workflow


def test_workflow_is_read_only_and_artifacts_are_scoped() -> None:
    workflow = (ROOT / ".github/workflows/ios-ci.yml").read_text()
    _assert_workflow_permissions(workflow)
    _assert_artifact_scope(workflow)


def test_scripts_are_executable_and_have_bash_syntax() -> None:
    scripts = [ROOT / "ios/scripts/ci-probe.sh", ROOT / "ios/scripts/ci-build-test.sh"]
    for script in scripts:
        assert script.stat().st_mode & stat.S_IXUSR
        assert "set -euo pipefail" in script.read_text()
        subprocess.run(["bash", "-n", str(script)], check=True)
    build_script = scripts[1].read_text()
    starts = [
        build_script.index("xcodebuild -list"),
        build_script.index("xcodebuild build-for-testing"),
        build_script.index("if xcodebuild test-without-building"),
    ]
    for start in starts:
        invocation = build_script[start : build_script.find("\n\n", start)]
        assert '-project "$PROJECT"' in invocation


def test_build_script_skips_when_project_spec_is_absent() -> None:
    script = ROOT / "ios/scripts/ci-build-test.sh"
    with tempfile.TemporaryDirectory() as temp_dir:
        env = {**os.environ, "ROOT": temp_dir}
        result = subprocess.run(
            ["bash", str(script)],
            cwd=temp_dir,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    assert result.stdout.strip() == "skip: ios/project.yml not found"


def _run_secret_scanner(
    root: Path,
    environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str, str]:
    _SCANNER["main"].__globals__["DIAGNOSTICS_DIR"] = root / "project/ios/build/ci"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(os, "environ", environment)
    result = _SCANNER["main"]()
    output = capsys.readouterr()
    return result, output.out, output.err


ScannerTestContext = tuple[Path, pytest.MonkeyPatch, pytest.CaptureFixture[str]]


def _check_secret_matches(context: ScannerTestContext) -> None:
    tmp_path, monkeypatch, capsys = context
    raw_secret, encoded_secret = "fake-pass", "fake key"
    diagnostics = tmp_path / "project/ios/build/ci"
    files = {
        diagnostics / "logs/test.log": f"failed login: {raw_secret}",
        diagnostics / "logs/fake%20key.log": "safe file content",
        diagnostics / "logs/encoded-content.log": "failed login: fake%20key",
    }
    for path, contents in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    output_file = tmp_path / "github-output"
    env = {
        "TEST_RUNNER_TRYINVOICE_SEAM_PASSWORD": raw_secret,
        "TEST_RUNNER_TRYINVOICE_SEAM_SUPABASE_ANON_KEY": encoded_secret,
        "GITHUB_OUTPUT": str(output_file),
    }
    result, stdout, stderr = _run_secret_scanner(tmp_path, env, monkeypatch, capsys)
    assert result == 1
    assert set(stdout.splitlines()) == {
        "logs/test.log",
        "[redacted path]",
        "logs/encoded-content.log",
    }
    assert len(stdout.splitlines()) == 3 and stdout.count("[redacted path]") == 1
    assert all(secret not in stdout + stderr for secret in (raw_secret, encoded_secret))
    assert not any(path.exists() for path in files)
    assert output_file.read_text() == "scan_complete=true\n"


def _check_safe_and_missing_secrets(context: ScannerTestContext) -> None:
    tmp_path, monkeypatch, capsys = context
    root = tmp_path / "configured"
    safe_file = root / "project/ios/build/ci/safe.log"
    safe_file.parent.mkdir(parents=True)
    safe_file.write_text("safe diagnostic", encoding="utf-8")
    output_file = root / "github-output"
    result, stdout, _ = _run_secret_scanner(
        root,
        {
            "TEST_RUNNER_TRYINVOICE_SEAM_PASSWORD": "synthetic-configured-password",
            "GITHUB_OUTPUT": str(output_file),
        },
        monkeypatch,
        capsys,
    )
    assert result == 0 and stdout == ""
    assert safe_file.read_text() == "safe diagnostic"
    assert output_file.read_text() == "scan_complete=true\n"
    root = tmp_path / "missing"
    output_file = root / "github-output"
    result, stdout, _ = _run_secret_scanner(
        root, {"GITHUB_OUTPUT": str(output_file)}, monkeypatch, capsys
    )
    assert result == 0 and stdout.strip() == "skip: no configured test secrets"
    assert output_file.read_text() == "scan_complete=true\n"


def _check_fail_closed_scans(context: ScannerTestContext) -> None:
    tmp_path, monkeypatch, capsys = context
    env = {"TEST_RUNNER_TRYINVOICE_SEAM_PASSWORD": "synthetic secret"}
    for index, relative in enumerate(
        ("ios/build", "ios/build/ci", "ios/build/ci/logs/linked")
    ):
        root = tmp_path / str(index)
        secret_file = root / "outside/private.log"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("synthetic secret", encoding="utf-8")
        link = root / "project" / relative
        link.parent.mkdir(parents=True)
        link.symlink_to(secret_file.parent, target_is_directory=True)
        output_file = root / "github-output"
        env["GITHUB_OUTPUT"] = str(output_file)
        result, stdout, stderr = _run_secret_scanner(root, env, monkeypatch, capsys)
        assert result == 2 and link.is_symlink() and secret_file.exists()
        assert not output_file.exists()
        assert ("logs/linked" in stdout) == relative.endswith("linked")
        assert "synthetic secret" not in stdout + stderr
    diagnostics = tmp_path / "project/ios/build/ci"
    logs = diagnostics / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    secret_file = logs / "test.log"
    secret_file.write_text("synthetic configured secret", encoding="utf-8")
    output_file = tmp_path / "failed-github-output"
    original_scandir = os.scandir

    def fail_logs_enumeration(path: Any) -> Any:
        if Path(path) == logs:
            raise PermissionError("synthetic enumeration failure")
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", fail_logs_enumeration)
    env["GITHUB_OUTPUT"] = str(output_file)
    result, _, _ = _run_secret_scanner(tmp_path, env, monkeypatch, capsys)
    assert (result, secret_file.exists(), output_file.exists()) == (2, True, False)


def test_secret_scanner_removes_matches_without_printing_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = (tmp_path, monkeypatch, capsys)
    _check_secret_matches(context)
    _check_safe_and_missing_secrets(context)
    _check_fail_closed_scans(context)
