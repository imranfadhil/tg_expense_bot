from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = "000000000:TEST_TOKEN"
    bot_passphrase: Optional[str] = None

    postgres_user: str = "expense_user"
    postgres_password: str = "expense_secret_password"
    postgres_db: str = "family_expenses"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url: Optional[str] = None

    default_currency: str = "$"
    timezone: str = "Asia/Singapore"

    webapp_enabled: bool = True
    webapp_host: str = "0.0.0.0"
    webapp_port: int = 8090
    webapp_url: Optional[str] = None
    tunnel_metrics_url: str = "http://tunnel:2000/metrics"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def passphrase_hash(self) -> Optional[str]:
        if not self.bot_passphrase or not self.bot_passphrase.strip():
            return None
        return hashlib.sha256(self.bot_passphrase.strip().encode("utf-8")).hexdigest()

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception:
            return ZoneInfo("UTC")

    def now(self) -> datetime:
        return datetime.now(self.tz)


settings = Settings()

