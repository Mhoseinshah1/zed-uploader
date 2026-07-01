"""Reply keyboards (persistent bottom buttons)."""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.bot import messages


def build_admin_menu() -> ReplyKeyboardMarkup:
    """The 2×2 admin reply keyboard (button texts come from messages.py)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=messages.BTN_UPLOAD),
                KeyboardButton(text=messages.BTN_MY_FILES),
            ],
            [
                KeyboardButton(text=messages.BTN_STATS),
                KeyboardButton(text=messages.BTN_SETTINGS),
            ],
        ],
        resize_keyboard=True,
    )
