"""Application configuration loaded from environment via pydantic-settings.

All user secrets come from the environment / .env file and are never hardcoded.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings for the whole project.

    Field names map (case-insensitively) to the UPPER_CASE variables in
    ``.env`` / the process environment.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- general ---------------------------------------------------------
    project_name: str = "ZedUploader"
    log_level: str = "INFO"

    # --- telegram bot ----------------------------------------------------
    # Default "" so bare imports never crash without env; bot creation still
    # fails clearly at runtime if the token is missing/invalid.
    bot_token: str = ""
    bot_username: str = "your_bot_username"
    admin_ids: str = ""
    bot_mode: str = "webhook"  # "webhook" | "polling"

    # --- webhook / domain ------------------------------------------------
    domain: str = "https://example.com"
    webhook_path: str = "/telegram/webhook"
    webhook_secret: str = "change_this_secret"

    # --- infrastructure --------------------------------------------------
    database_url: str = (
        "postgresql+asyncpg://uploader:uploader_password@db:5432/uploader_bot"
    )
    redis_url: str = "redis://redis:6379/0"

    # --- api security ----------------------------------------------------
    api_key: str = "change_this_api_key"
    jwt_secret: str = "change_this_jwt_secret"

    # --- media defaults --------------------------------------------------
    default_protect_content: bool = False
    default_auto_delete_seconds: int = 0
    default_plan: str = "free"

    # --- postgres service (kept in sync with DATABASE_URL) ---------------
    postgres_user: str = "uploader"
    postgres_password: str = "uploader_password"
    postgres_db: str = "uploader_bot"

    @property
    def admin_id_list(self) -> list[int]:
        """Parse the comma-separated ``ADMIN_IDS`` string into ints.

        Non-numeric entries are ignored rather than raising.
        """
        return [
            int(part)
            for part in self.admin_ids.replace(" ", "").split(",")
            if part.isdigit()
        ]

    @property
    def webhook_url(self) -> str:
        """Full webhook URL = DOMAIN + WEBHOOK_PATH."""
        return f"{self.domain.rstrip('/')}{self.webhook_path}"


settings = Settings()
