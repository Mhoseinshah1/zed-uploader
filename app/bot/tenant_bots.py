"""TenantBotProvider — per-process cache of tenant Bots for background workers.

The API process holds the live registry (app/bot/registry.py); the WORKER runs
in a separate process, so it builds each tenant's Bot itself from the decrypted
token (the platform tenant uses the env token). Returns None for a
missing/suspended tenant so the caller can drop the job gracefully instead of
acting with the wrong bot. Tokens are only decrypted in memory, never logged.
"""
from __future__ import annotations

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.tenant_context import PLATFORM_TENANT_ID
from app.models.tenant import Tenant
from app.services.tenant_service import TenantService

log = get_logger("tenant_bots")


class TenantBotProvider:
    def __init__(self) -> None:
        self._bots: dict[int, Bot] = {}

    async def get(self, session: AsyncSession, tenant_id: int) -> Bot | None:
        """Return the tenant's Bot (cached), or None if it can't serve.

        The caller must already be in a tenant/ALL context so the Tenant lookup
        (a global table) is permitted by the guard.
        """
        if tenant_id in self._bots:
            return self._bots[tenant_id]
        if tenant_id == PLATFORM_TENANT_ID:
            token = settings.bot_token or None
        else:
            tenant = await session.scalar(
                select(Tenant).where(Tenant.id == tenant_id)
            )
            if tenant is None or tenant.status != "active":
                return None  # gone/suspended -> drop the job gracefully
            token = TenantService.decrypt_token(tenant)
        if not token:
            return None
        bot = Bot(token=token)
        self._bots[tenant_id] = bot
        return bot

    async def close(self) -> None:
        for bot in self._bots.values():
            try:
                await bot.session.close()
            except Exception:
                pass
        self._bots.clear()
