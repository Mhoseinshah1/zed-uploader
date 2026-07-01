"""Reply keyboards (persistent bottom buttons)."""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.bot import messages


def build_admin_menu(is_owner: bool = False) -> ReplyKeyboardMarkup:
    """Admin reply keyboard. Owners get an extra management section."""
    keyboard = [
        [
            KeyboardButton(text=messages.BTN_UPLOAD),
            KeyboardButton(text=messages.BTN_BATCH_UPLOAD),
        ],
        [
            KeyboardButton(text=messages.BTN_MY_FILES),
            KeyboardButton(text=messages.BTN_STATS),
        ],
        [KeyboardButton(text=messages.BTN_SETTINGS)],
    ]
    if is_owner:
        keyboard.append(
            [
                KeyboardButton(text=messages.BTN_CHANNELS),
                KeyboardButton(text=messages.BTN_ADMINS),
            ]
        )
        keyboard.append([KeyboardButton(text=messages.BTN_BROADCAST)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
