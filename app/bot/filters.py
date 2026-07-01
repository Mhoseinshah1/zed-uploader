"""Custom aiogram filters."""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from app.core.config import settings


class IsAdmin(BaseFilter):
    """Pass only when the event's sender id is in ADMIN_IDS.

    Works for any event carrying ``from_user`` (Message, CallbackQuery, …); the
    event is passed positionally by aiogram, so a single implementation covers
    both message and callback_query handlers.
    """

    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and user.id in settings.admin_id_list
