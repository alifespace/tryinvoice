import re
from pathlib import Path

from app.config import Settings

ROOT_DIR = Path(__file__).parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
RENDER_CONFIG = (ROOT_DIR / "render.yaml").read_text()
MANUAL = (ROOT_DIR / "docs/environments/render-test-backend.md").read_text()
SECRET_VARS = {
    "DATABASE_URL",
    "SUPABASE_URL",
    "SUPABASE_JWT_SECRET",
    "STORAGE_ENDPOINT_URL",
    "STORAGE_BUCKET",
    "STORAGE_ACCESS_KEY_ID",
    "STORAGE_SECRET_ACCESS_KEY",
}
PLATFORM_VARS = {"PORT"}
OPTIONAL_DERIVED_VARS = {"SUPABASE_JWKS_URL", "SUPABASE_JWT_ISSUER"}


def _render_env_vars() -> list[tuple[str, str, str]]:
    header = (
        "services:\n  - type: web\n    name: tryinvoice-test-api\n"
        "    runtime: docker\n    plan: free\n    region: oregon\n"
        "    dockerfilePath: ./backend/Dockerfile\n"
        "    dockerContext: ./backend\n"
        "    healthCheckPath: /api/v1/health\n    autoDeploy: false\n"
        "    envVars:\n"
    )
    assert RENDER_CONFIG.startswith(header)
    block = RENDER_CONFIG.removeprefix(header)
    entry = (
        r"      - key: ([A-Z][A-Z0-9_]*)\n"
        r"        (value|sync): ([^\n]+)\n"
    )
    matches = list(re.finditer(entry, block))
    assert matches and "".join(match.group() for match in matches) == block
    return [match.groups() for match in matches]


def test_render_config_declares_one_free_docker_web_service() -> None:
    assert _render_env_vars()
    assert (BACKEND_DIR / "Dockerfile").is_file()


def test_render_health_check_matches_dockerfile() -> None:
    dockerfile = (BACKEND_DIR / "Dockerfile").read_text()
    healthcheck = re.search(
        r"HEALTHCHECK\b.*?urlopen\('http://127\.0\.0\.1:(\d+)(/[^']+)'",
        dockerfile,
    )
    assert healthcheck is not None
    assert healthcheck.group(1) == "8000"
    assert healthcheck.group(2) == "/api/v1/health"


def test_render_config_has_no_unsupported_or_implicit_deploy_settings() -> None:
    assert not re.search(
        r"^[ \t]*(disk|preDeployCommand|(start|docker)Command|databases|image):|latest",
        RENDER_CONFIG,
        re.MULTILINE | re.IGNORECASE,
    )


def test_render_env_vars_match_settings_and_platform_contract() -> None:
    fields = set(map(str.upper, Settings.model_fields))
    assert PLATFORM_VARS == {"PORT"} and fields.isdisjoint(PLATFORM_VARS)
    assert "LOCAL_DEV_USER_ID" in fields
    expected = (fields - {"LOCAL_DEV_USER_ID"} - OPTIONAL_DERIVED_VARS) | PLATFORM_VARS
    env_vars = _render_env_vars()
    names = [name for name, _, _ in env_vars]
    assert sorted(names) == sorted(expected)
    assert "LOCAL_DEV_USER_ID" not in RENDER_CONFIG
    declarations = {name: (kind, value) for name, kind, value in env_vars}
    default_keys = "APP_ENV PORT STORAGE_REGION STORAGE_PRESIGN_TTL_SECONDS".split()
    assert tuple(map(declarations.__getitem__, default_keys)) == (
        ("value", "staging"),
        ("value", '"8000"'),
        ("value", "auto"),
        ("value", '"900"'),
    )
    assert {
        name
        for name, (kind, value) in declarations.items()
        if kind == "sync" and value == "false"
    } == SECRET_VARS
    assert not re.search(r"SUPABASE_(?:JWKS_URL|JWT_ISSUER)", RENDER_CONFIG)


def test_render_manual_lists_secrets_and_preserves_existing_resources() -> None:
    assert len(MANUAL.splitlines()) <= 120
    assert SECRET_VARS <= set(re.findall(r"`([A-Z][A-Z0-9_]+)`", MANUAL))
    assert all(
        term in MANUAL
        for term in ("floor-plans", "project-renders", "旧表、旧用户、旧 bucket")
    )


def test_render_manual_contains_no_credential_values() -> None:
    credential_pattern = r"postgres(?:ql)?://[^\s:@]+:[^\s@]+@|\bsk-|\beyJ"
    assert all(
        not re.search(credential_pattern, content)
        for content in (MANUAL, RENDER_CONFIG)
    )
