"""Reply keyboards (persistent bottom buttons)."""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.bot import messages


def build_user_menu() -> ReplyKeyboardMarkup:
    """Menu for regular (non-admin) users: wallet + subscription."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=messages.BTN_WALLET),
                KeyboardButton(text=messages.BTN_SUBSCRIPTION),
            ]
        ],
        resize_keyboard=True,
    )


def build_admin_menu(is_owner: bool = False) -> ReplyKeyboardMarkup:
    """Admin reply keyboard. Owners get an extra management/sell section."""
    keyboard = [
        [
            KeyboardButton(text=messages.BTN_UPLOAD),
            KeyboardButton(text=messages.BTN_BATCH_UPLOAD),
        ],
        [
            KeyboardButton(text=messages.BTN_MY_FILES),
            KeyboardButton(text=messages.BTN_STATS),
        ],
        [
            KeyboardButton(text=messages.BTN_WALLET),
            KeyboardButton(text=messages.BTN_SUBSCRIPTION),
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
        keyboard.append(
            [
                KeyboardButton(text=messages.BTN_BROADCAST),
                KeyboardButton(text=messages.BTN_SELL),
            ]
        )
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
