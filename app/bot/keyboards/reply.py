"""Reply keyboards (persistent bottom buttons)."""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.bot import messages


def build_user_menu(is_platform: bool = False) -> ReplyKeyboardMarkup:
    """Menu for regular (non-admin) users: wallet + subscription.

    On the platform (master) bot only, a "ساخت ربات" button lets any user buy
    their own hosted bot (F3); customer bots never show it.
    """
    keyboard = [
        [
            KeyboardButton(text=messages.BTN_WALLET),
            KeyboardButton(text=messages.BTN_SUBSCRIPTION),
        ],
        [KeyboardButton(text=messages.BTN_SUPPORT)],
    ]
    if is_platform:
        keyboard.append([KeyboardButton(text=messages.BTN_CREATE_BOT)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def build_admin_menu(is_owner: bool = False, is_platform: bool = False) -> ReplyKeyboardMarkup:
    """Admin reply keyboard. Owners get an extra management/sell section; on the
    platform bot a "ساخت ربات" button is appended."""
    keyboard = [
        [
            KeyboardButton(text=messages.BTN_UPLOAD),
            KeyboardButton(text=messages.BTN_BATCH_UPLOAD),
        ],
        [
            KeyboardButton(text=messages.BTN_MY_FILES),
            KeyboardButton(text=messages.BTN_FOLDERS),
        ],
        [
            KeyboardButton(text=messages.BTN_STATS),
            KeyboardButton(text=messages.BTN_SEARCH),
        ],
        [
            KeyboardButton(text=messages.BTN_WALLET),
            KeyboardButton(text=messages.BTN_SUBSCRIPTION),
        ],
        [
            KeyboardButton(text=messages.BTN_SETTINGS),
            KeyboardButton(text=messages.BTN_REVIEW),
        ],
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
        keyboard.append(
            [
                KeyboardButton(text=messages.BTN_ADS),
                KeyboardButton(text=messages.BTN_LOG_GROUP),
            ]
        )
    keyboard.append(
        [
            KeyboardButton(text=messages.BTN_PANEL),
            KeyboardButton(text=messages.BTN_SUPPORT),
        ]
    )
    if is_platform:
        keyboard.append([KeyboardButton(text=messages.BTN_CREATE_BOT)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
