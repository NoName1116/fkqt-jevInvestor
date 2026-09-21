from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    environment: str = Field(default="local", validation_alias="JEV_INVESTOR_ENV")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/fkqt_jevinvestor.db",
        validation_alias="JEV_INVESTOR_DATABASE_URL",
    )
    typesafe_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="TYPESAFE_API_KEY",
    )
    typesafe_model: str = Field(default="jev-latest", validation_alias="TYPESAFE_MODEL")
    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="DEEPSEEK_API_KEY",
    )
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias="DEEPSEEK_BASE_URL",
    )
    deepseek_model: str = Field(
        default="deepseek-flash",
        validation_alias="DEEPSEEK_MODEL",
    )
    deepseek_reasoning_effort: Literal["low", "high"] = Field(
        default="high",
        validation_alias="DEEPSEEK_REASONING_EFFORT",
    )
    deepseek_timeout_seconds: float = Field(
        default=30,
        gt=0,
        validation_alias="DEEPSEEK_TIMEOUT_SECONDS",
    )
    fkqt_manifest_bundle_root: Path | None = Field(
        default=None,
        validation_alias="FKQT_MANIFEST_BUNDLE_ROOT",
    )
    market_snapshot_root: Path = Field(
        default=Path("data/snapshots"),
        validation_alias="JEV_INVESTOR_MARKET_SNAPSHOT_ROOT",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
