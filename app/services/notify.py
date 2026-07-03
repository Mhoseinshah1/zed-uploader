"""Best-effort user notifications from the panel (I3).

Builds a throwaway Bot from the CURRENT tenant's token (the platform tenant uses
the env token) and DMs a user. Never raises into the caller — a delivery failure
must not break the panel action that triggered it. The bot token is never logged.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

log = get_logger("notify")


async def notify_user(session: AsyncSession, user_id: int, text: str) -> bool:
    """DM ``text`` to the user via the current tenant's bot. Returns success."""
    try:
        from aiogram import Bot

        from app.core.config import settings
        from app.core.tenant_context import PLATFORM_TENANT_ID, current_tenant
        from app.models.tenant import Tenant
        from app.models.user import User
        from app.services.tenant_service import TenantService

        user = await session.get(User, user_id)
        if user is None:
            return False
        tid = current_tenant()
        token = None
        if isinstance(tid, int):
            tenant = await session.scalar(select(Tenant).where(Tenant.id == tid))
            token = TenantService.decrypt_token(tenant) if tenant else None
            if not token and tid == PLATFORM_TENANT_ID:
                token = settings.bot_token or None
        if not token:
            return False
        bot = Bot(token=token)
        try:
            await bot.send_message(user.telegram_id, text)
            return True
        finally:
            try:
                await bot.session.close()
            except Exception:
                pass
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("notify_user_failed", user_id=user_id, error=str(exc))
        return False
