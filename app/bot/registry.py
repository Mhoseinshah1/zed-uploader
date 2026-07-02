"""In-memory multi-bot registry (Phase F2).

Holds one aiogram ``Bot`` per active customer tenant, keyed by ``bot_id``, so
the per-tenant webhook route can dispatch an update to the right bot under the
right tenant context. The platform bot is NOT in the registry — it keeps its
dedicated ``app.state.bot`` + legacy ``/telegram/webhook`` route (its token
comes from the env, and its already-configured Telegram webhook path must stay
stable). Customer bots are handled uniformly among themselves.

All Telegram I/O is best-effort: a bad token / network hiccup marks the tenant
``suspended`` and logs, and never crashes startup or a request. Tokens are
decrypted only in memory to build the Bot and are never logged.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.logging import get_logger
from app.core.tenant_context import PLATFORM_TENANT_ID, all_tenants
from app.services.tenant_service import TenantService

log = get_logger("registry")


def tenant_webhook_url(bot_id: int) -> str:
    return f"{settings.domain.rstrip('/')}/tenant/{bot_id}/webhook"


@dataclass
class RegisteredBot:
    tenant_id: int
    bot_id: int
    bot: Bot
    secret: str


class BotRegistry:
    """bot_id -> RegisteredBot. One instance per API process (on app.state)."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker
        self._bots: dict[int, RegisteredBot] = {}

    def get(self, bot_id: int) -> RegisteredBot | None:
        return self._bots.get(bot_id)

    def bot_ids(self) -> list[int]:
        return list(self._bots)

    async def _ensure_secret(self, session: AsyncSession, tenant) -> str:
        """Return the tenant's webhook secret, generating+persisting if absent."""
        if tenant.webhook_secret:
            return tenant.webhook_secret
        secret = secrets.token_urlsafe(24)
        tenant.webhook_secret = secret
        await session.commit()
        return secret

    async def register(self, session: AsyncSession, tenant) -> RegisteredBot | None:
        """Build the tenant's Bot, set its webhook, and add it to the registry.

        Returns None (and marks the tenant suspended) on a token/Telegram error.
        The caller's ``session`` must already be in a context that can read the
        tenant (its own tenant or ALL).
        """
        if tenant.id == PLATFORM_TENANT_ID:
            return None  # the platform bot is served on its dedicated route
        if not tenant.bot_id or not tenant.bot_token:
            return None
        try:
            token = TenantService.decrypt_token(tenant)
            secret = await self._ensure_secret(session, tenant)
            bot = Bot(token=token)
            await bot.set_webhook(
                url=tenant_webhook_url(tenant.bot_id),
                secret_token=secret,
                drop_pending_updates=False,
                allowed_updates=["message", "callback_query"],
            )
        except Exception as exc:
            log.warning("tenant_bot_register_failed", tenant_id=tenant.id, error=str(exc))
            await TenantService(session).set_status(tenant.id, "suspended")
            return None
        entry = RegisteredBot(
            tenant_id=tenant.id, bot_id=tenant.bot_id, bot=bot, secret=secret
        )
        self._bots[tenant.bot_id] = entry
        log.info("tenant_bot_registered", tenant_id=tenant.id, bot_id=tenant.bot_id)
        return entry

    async def unregister(self, bot_id: int) -> None:
        """Drop a bot: delete its webhook and close its session (best-effort)."""
        entry = self._bots.pop(bot_id, None)
        if entry is None:
            return
        try:
            await entry.bot.delete_webhook(drop_pending_updates=False)
        except Exception as exc:
            log.warning("tenant_bot_unregister_failed", bot_id=bot_id, error=str(exc))
        try:
            await entry.bot.session.close()
        except Exception:
            pass
        log.info("tenant_bot_unregistered", tenant_id=entry.tenant_id, bot_id=bot_id)

    async def load_active(self) -> None:
        """Startup: register every active customer tenant (best-effort)."""
        try:
            with all_tenants():
                async with self._session_maker() as session:
                    tenants = await TenantService(session).list_active()
                    for tenant in tenants:
                        await self.register(session, tenant)
        except Exception as exc:  # never block startup (e.g. no DB offline)
            log.warning("registry_load_failed", error=str(exc))

    async def reload(self, tenant_id: int) -> None:
        """Add/remove a bot live after its status/token changed (F3 + suspend)."""
        with all_tenants():
            async with self._session_maker() as session:
                tenant = await TenantService(session).get(tenant_id)
                if tenant is None or tenant.status != "active":
                    if tenant is not None and tenant.bot_id:
                        await self.unregister(tenant.bot_id)
                    return
                if tenant.bot_id in self._bots:
                    return  # already registered
                await self.register(session, tenant)

    async def close(self) -> None:
        for bot_id in list(self._bots):
            entry = self._bots.pop(bot_id)
            try:
                await entry.bot.session.close()
            except Exception:
                pass
