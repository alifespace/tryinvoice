from pathlib import Path

import pytest

from app.config import get_settings


def test_settings_reads_environment_and_masks_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = "postgresql://localhost/tryinvoice"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == database_url
    assert database_url not in str(settings.database_url)
    assert database_url not in repr(settings)
    get_settings.cache_clear()


def test_settings_are_optional_when_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.database_url is None
    assert settings.supabase_url is None
    assert settings.supabase_jwt_secret is None
    assert settings.app_env == "local"
    get_settings.cache_clear()
