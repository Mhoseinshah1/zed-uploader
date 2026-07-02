"""TenantContextMiddleware — set the tenant context for each bot update.

Registered BEFORE DbSessionMiddleware (so it is outermost) — every query/insert
in a handler, filter, or the user-upsert middleware runs tenant-scoped and fails
closed otherwise. The tenant is taken from the ``tenant_id`` passed to
``dp.feed_update(bot, update, tenant_id=...)`` by the per-tenant webhook route
(F2); the legacy platform webhook and polling omit it, so it defaults to the
platform tenant (the single bot behaves exactly as before).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.core.tenant_context import PLATFORM_TENANT_ID, reset_tenant, set_tenant


class TenantContextMiddleware(BaseMiddleware):
    def __init__(self, default_tenant_id: int = PLATFORM_TENANT_ID) -> None:
        self.default_tenant_id = default_tenant_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tenant_id = data.get("tenant_id", self.default_tenant_id)
        token = set_tenant(tenant_id)
        try:
            return await handler(event, data)
        finally:
            reset_tenant(token)
