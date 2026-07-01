"""Service over the ``bot_settings`` key/value table.

Values are stored as text; helpers coerce to bool/int. Effective-default helpers
fall back to the env-provided settings when a key is unset.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.settings import BotSetting

KEY_PROTECT = "default_protect_content"
KEY_AUTODELETE = "default_auto_delete_seconds"

_TRUE_VALUES = {"1", "true", "yes", "on"}


class BotSettingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _row(self, key: str) -> BotSetting | None:
        return await self.session.scalar(select(BotSetting).where(BotSetting.key == key))

    async def get_raw(self, key: str) -> str | None:
        row = await self._row(key)
        return row.value if row else None

    async def get_bool(self, key: str, default: bool) -> bool:
        raw = await self.get_raw(key)
        if raw is None:
            return default
        return raw.strip().lower() in _TRUE_VALUES

    async def get_int(self, key: str, default: int) -> int:
        raw = await self.get_raw(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    async def set(self, key: str, value: object) -> None:
        """Upsert a setting; booleans stored as 'true'/'false'."""
        stored = "true" if value is True else "false" if value is False else str(value)
        row = await self._row(key)
        if row is None:
            self.session.add(BotSetting(key=key, value=stored))
        else:
            row.value = stored
        await self.session.commit()

    # --- effective defaults (env fallback) -------------------------------
    async def effective_protect(self) -> bool:
        return await self.get_bool(KEY_PROTECT, settings.default_protect_content)

    async def effective_autodelete(self) -> int:
        return await self.get_int(KEY_AUTODELETE, settings.default_auto_delete_seconds)
