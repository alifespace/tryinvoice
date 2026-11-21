import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _assert_workflow_permissions(workflow: str) -> None:
    assert "runs-on: macos-15" in workflow
    assert "workflow_dispatch:" in workflow
    assert re.search(r"(?m)^  contents: read$", workflow)
    assert "latest" not in workflow.lower()
    assert "secrets." not in workflow
    assert "persist-credentials: false" in workflow


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
