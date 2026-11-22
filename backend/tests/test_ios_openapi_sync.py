import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "ios/scripts/sync-openapi-types.py"
CONTRACT = ROOT / "contracts/openapi.json"
GENERATED = ROOT / "ios/TryInvoice/Core/API/APITypes.generated.swift"

_SPEC = importlib.util.spec_from_file_location("sync_openapi_types", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_SYNC = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SYNC)


def test_generated_file_matches_script_and_check_mode() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert _SYNC.render_types(contract) == GENERATED.read_text(encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_check_mode_fails_for_stale_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "APITypes.generated.swift"
    output.write_text("stale\n", encoding="utf-8")

    result = _SYNC.main(
        ["--check"],
        contract_path=CONTRACT,
        output_path=output,
    )

    assert result == 1
    assert "out of date" in capsys.readouterr().err


def test_generated_models_keep_field_order_and_contract_optionality() -> None:
    generated = _SYNC.render_types(json.loads(CONTRACT.read_text(encoding="utf-8")))
    batch_create = generated.split("struct BatchCreate:", maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]
    batch_out = generated.split("struct BatchOut:", maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]

    assert (
        batch_create.index("let batchDate: String")
        < batch_create.index("let expenseType: String")
        < batch_create.index("let templateId: String")
        < batch_create.index("let title: String")
    )
    assert 'case batchDate = "batch_date"' in batch_create
    assert "let tripEndDate: String?" in batch_out
    assert "let tripStartDate: String?" in batch_out
    assert "let loc: [JSONValue]" in generated


def test_contract_schema_change_changes_generated_output() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    baseline = _SYNC.render_types(contract)
    contract["components"]["schemas"]["BatchCreate"]["properties"]["title"]["type"] = (
        "integer"
    )

    changed = _SYNC.render_types(contract)

    assert changed != baseline
    assert "let title: Int" in changed
