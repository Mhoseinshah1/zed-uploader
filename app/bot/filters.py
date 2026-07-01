"""Custom aiogram filters."""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message

from app.core.config import settings


class IsAdmin(BaseFilter):
    """Pass only when the sender's Telegram id is in ADMIN_IDS."""

    async def __call__(self, message: Message) -> bool:
        user = message.from_user
        if user is None:
            return False
        return user.id in settings.admin_id_list
