"""BlockedUserMiddleware (I1) — enforce ``User.is_blocked``.

Runs AFTER UserContextMiddleware, so ``data['db_user']`` is resolved. If the
resolved user is blocked AND is NOT an admin/owner of this tenant, ALL handler
processing is stopped and the user gets a single Persian notice. Admins/owners
(env owners or any active Admin row for this tenant) bypass the block so a
mistakenly-blocked operator can still manage the bot.

A blocked purchase is refused at the source too: ``pre_checkout_query`` is
answered ``ok=False`` so an old Stars invoice can never complete.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.services.admin_service import AdminService


class BlockedUserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        db_user = data.get("db_user")
        if db_user is not None and getattr(db_user, "is_blocked", False):
            session = data.get("session")
            is_admin = False
            if session is not None:
                is_admin = await AdminService.is_admin(session, db_user.telegram_id)
            if not is_admin:
                await self._deny(event)
                return None  # stop all handler processing
        return await handler(event, data)

    async def _deny(self, event: TelegramObject) -> None:
        """Tell the blocked user once, in the way that fits the update type."""
        from app.bot import messages

        text = messages.ACCOUNT_BLOCKED
        try:
            pre_checkout = getattr(event, "pre_checkout_query", None)
            callback = getattr(event, "callback_query", None)
            message = getattr(event, "message", None)
            if pre_checkout is not None:
                await pre_checkout.answer(ok=False, error_message=text)
            elif callback is not None:
                await callback.answer(text, show_alert=True)
            elif message is not None:
                await message.answer(text)
        except Exception:  # never let the block notice raise
            pass
