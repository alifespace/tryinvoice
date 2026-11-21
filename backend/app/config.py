from functools import lru_cache
from uuid import UUID

from pydantic import SecretStr
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
    app_env: str = "local"
    local_dev_user_id: UUID | None = None
    storage_endpoint_url: str | None = None
    storage_bucket: str | None = None
    storage_access_key_id: SecretStr | None = None
    storage_secret_access_key: SecretStr | None = None
    storage_region: str = "auto"
    storage_presign_ttl_seconds: int = 900


@lru_cache
def get_settings() -> Settings:
    return Settings()
