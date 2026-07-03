"""MaintenanceMiddleware (J7) — per-tenant maintenance mode.

Runs AFTER user_context (so ``db_user``/``session`` are resolved), alongside
the blocked check. When the tenant's ``maintenance_mode`` setting is on, only
that tenant's admins/owners may use the bot; everyone else gets the (editable)
Persian maintenance message and all handler processing stops. Because the
setting is a tenant-scoped BotSetting read inside the update's tenant context,
one tenant in maintenance never affects another.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.services.admin_service import AdminService
from app.services.bot_setting_service import (
    KEY_MAINTENANCE_MESSAGE,
    KEY_MAINTENANCE_MODE,
    BotSettingService,
)


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session = data.get("session")
        if session is None:
            return await handler(event, data)
        setting = BotSettingService(session)
        if not await setting.get_bool(KEY_MAINTENANCE_MODE, False):
            return await handler(event, data)

        tg_user = data.get("event_from_user")
        if tg_user is not None and await AdminService.is_admin(session, tg_user.id):
            return await handler(event, data)  # admins/owners keep working

        await self._deny(event, session, setting)
        return None  # stop all handler processing

    async def _deny(self, event: TelegramObject, session, setting) -> None:
        from app.bot import messages

        text = (
            await setting.get_raw(KEY_MAINTENANCE_MESSAGE) or ""
        ).strip() or messages.MAINTENANCE_DEFAULT
        try:
            callback = getattr(event, "callback_query", None)
            message = getattr(event, "message", None)
            inline_query = getattr(event, "inline_query", None)
            pre_checkout = getattr(event, "pre_checkout_query", None)
            if pre_checkout is not None:
                await pre_checkout.answer(ok=False, error_message=text)
            elif callback is not None:
                await callback.answer(text, show_alert=True)
            elif message is not None:
                await message.answer(text)
            elif inline_query is not None:
                await inline_query.answer([], cache_time=5, is_personal=True)
        except Exception:  # the notice must never raise
            pass
