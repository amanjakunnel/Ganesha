from __future__ import annotations

from typing import Optional, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL
from sqlalchemy.engine.url import make_url

TELEGRAM_TOKEN_PLACEHOLDERS = frozenset({"", "your_bot_token_here", "changeme", "replace_me"})


class Settings(BaseSettings):
    """Application settings centralization.

    Loads configuration from .env by default. Environment variables will override
    values in the .env file when intentionally provided.
    """

    postgres_user: str = "job_agent"
    postgres_password: Optional[str] = None
    postgres_db: str = "job_agent"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url: Optional[str] = None  # Allow override with DATABASE_URL
    telegram_bot_token: Optional[str] = None
    telegram_allowed_user_id: Optional[int] = None
    telegram_allowed_chat_id: Optional[int] = None
    telegram_chat_id: Optional[int] = None  # legacy alias for TELEGRAM_CHAT_ID

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def _apply_legacy_telegram_aliases(self) -> Self:
        if self.telegram_allowed_chat_id is None and self.telegram_chat_id is not None:
            self.telegram_allowed_chat_id = self.telegram_chat_id
        return self

    def telegram_token_configured(self) -> bool:
        token = (self.telegram_bot_token or "").strip()
        return bool(token) and token not in TELEGRAM_TOKEN_PLACEHOLDERS

    def telegram_operator_restrictions(self) -> dict[str, str]:
        """Non-secret summary of Telegram auth constraints for doctor output."""
        user = (
            str(self.telegram_allowed_user_id)
            if self.telegram_allowed_user_id is not None
            else "not set (any user allowed)"
        )
        chat = (
            str(self.telegram_allowed_chat_id)
            if self.telegram_allowed_chat_id is not None
            else "not set (any chat allowed)"
        )
        return {"allowed_user_id": user, "allowed_chat_id": chat}

    def sqlalchemy_url(self) -> URL:
        """Construct a SQLAlchemy URL from individual settings.

        Use sqlalchemy.engine.URL.create to avoid encoding mistakes and make
        intent explicit. Do not log or expose the password in any helpers.

        If DATABASE_URL is set, use it directly. Otherwise construct from individual settings.
        """
        # Check for DATABASE_URL override first
        if self.database_url:
            # Parse a full URL string into a SQLAlchemy URL object safely.
            # URL.create does not accept a full URL string on some SQLAlchemy versions,
            # so use make_url to parse a string value.
            return make_url(self.database_url)

        # Use URL.create which returns an instance acceptable to SQLAlchemy
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    def redacted_sqlalchemy_url(self) -> str:
        """Return a string-safe redacted URL for logging without the password."""
        url = self.sqlalchemy_url()
        return url.render_as_string(hide_password=True)


# Singleton settings instance for convenience
settings = Settings()
