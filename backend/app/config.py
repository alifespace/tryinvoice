from functools import lru_cache
from uuid import UUID

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: SecretStr | None = None
    supabase_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None
    supabase_jwks_url: str | None = None
    supabase_jwt_issuer: str | None = None
    supabase_jwt_audience: str = "authenticated"
    supabase_jwks_cache_seconds: int = 600
    supabase_jwks_timeout_seconds: float = 3.0
    supabase_allow_legacy_hs256: bool = False
    app_env: str = "local"
    local_dev_user_id: UUID | None = None
    storage_endpoint_url: str | None = None
    storage_bucket: str | None = None
    storage_access_key_id: SecretStr | None = None
    storage_secret_access_key: SecretStr | None = None
    storage_region: str = "auto"
    storage_presign_ttl_seconds: int = 900

    @field_validator("supabase_jwt_audience", mode="before")
    @classmethod
    def _default_empty_jwt_audience(cls, value: object) -> object:
        return value or "authenticated"


@lru_cache
def get_settings() -> Settings:
    return Settings()
