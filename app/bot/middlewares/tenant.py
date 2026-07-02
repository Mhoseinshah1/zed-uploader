"""TenantContextMiddleware — set the tenant context for each bot update.

Phase F1: the single bot always runs as the platform tenant. Registered BEFORE
DbSessionMiddleware (so it is outermost) — every query/insert in a handler,
filter, or the user-upsert middleware runs tenant-scoped. F2 will pass the
resolved per-bot tenant id here instead of the constant.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.core.tenant_context import PLATFORM_TENANT_ID, reset_tenant, set_tenant


class TenantContextMiddleware(BaseMiddleware):
    def __init__(self, tenant_id: int = PLATFORM_TENANT_ID) -> None:
        self.tenant_id = tenant_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        token = set_tenant(self.tenant_id)
        try:
            return await handler(event, data)
        finally:
            reset_tenant(token)
