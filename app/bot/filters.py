"""Custom aiogram filters (DB-aware authz).

aiogram passes middleware data to filters, so these accept the injected
``session`` (set by DbSessionMiddleware) alongside the positional event.
"""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import is_platform
from app.services.admin_service import AdminService


class IsPlatform(BaseFilter):
    """Pass only inside the platform (master) bot context (H1 role isolation).

    Router/handler-level guard for platform-owner-only flows (the buy-a-bot
    seller flow, platform management). A customer/reseller bot serves under its
    own tenant context, so this is False there and the handler never runs.
    """

    async def __call__(self, event: TelegramObject) -> bool:
        return is_platform()


class IsAdmin(BaseFilter):
    """Pass for owners and active admins (env ids OR active Admin rows)."""

    async def __call__(self, event: TelegramObject, session: AsyncSession) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and await AdminService.is_admin(session, user.id)


class IsOwner(BaseFilter):
    """Pass only for owners (env ids OR active Admin rows with role 'owner')."""

    async def __call__(self, event: TelegramObject, session: AsyncSession) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and await AdminService.is_owner(session, user.id)
