"""UserContextMiddleware — upserts the User and injects ``db_user``.

Runs after DbSessionMiddleware, so ``data['session']`` is available.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.services.user_service import UserService


class UserContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session = data.get("session")
        tg_user = data.get("event_from_user")
        db_user = None
        if session is not None and tg_user is not None:
            db_user = await UserService(session).upsert_from_telegram(tg_user)
        data["db_user"] = db_user
        return await handler(event, data)
