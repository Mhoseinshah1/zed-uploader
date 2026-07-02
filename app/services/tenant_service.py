"""Tenant registry service (Phase F1).

Thin CRUD over the global ``tenants`` table, plus at-rest token crypto. The
registry itself is global, so callers run these under a real tenant context or
``all_tenants`` (the DB guard treats Tenant as unscoped either way). F2/F3 build
the webhook registry and buy-a-bot flow on top of this.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.tenant_context import PLATFORM_TENANT_ID
from app.models.tenant import Tenant


class TenantService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: int) -> Tenant | None:
        return await self.session.scalar(select(Tenant).where(Tenant.id == tenant_id))

    async def get_by_bot_id(self, bot_id: int) -> Tenant | None:
        return await self.session.scalar(select(Tenant).where(Tenant.bot_id == bot_id))

    async def platform(self) -> Tenant | None:
        return await self.get(PLATFORM_TENANT_ID)

    async def list_active(self) -> list[Tenant]:
        rows = await self.session.scalars(
            select(Tenant).where(Tenant.status == "active").order_by(Tenant.id)
        )
        return list(rows.all())

    async def create(
        self,
        *,
        owner_user_id: int | None,
        bot_id: int | None,
        bot_username: str | None,
        bot_token: str | None,
        plan: str | None = None,
        expires_at: datetime | None = None,
        webhook_secret: str | None = None,
        status: str = "active",
    ) -> Tenant:
        """Create a tenant, encrypting the bot token at rest."""
        tenant = Tenant(
            owner_user_id=owner_user_id,
            bot_id=bot_id,
            bot_username=bot_username,
            bot_token=encrypt_secret(bot_token) if bot_token else None,
            plan=plan,
            expires_at=expires_at,
            webhook_secret=webhook_secret,
            status=status,
        )
        self.session.add(tenant)
        await self.session.commit()
        return tenant

    async def set_status(self, tenant_id: int, status: str) -> bool:
        tenant = await self.get(tenant_id)
        if tenant is None:
            return False
        tenant.status = status
        await self.session.commit()
        return True

    @staticmethod
    def decrypt_token(tenant: Tenant) -> str | None:
        """Decrypt a tenant's stored bot token (never logged)."""
        return decrypt_secret(tenant.bot_token) if tenant.bot_token else None
