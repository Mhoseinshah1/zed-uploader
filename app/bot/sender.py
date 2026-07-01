"""Message sending helpers.

Auto-deletion is handled by the Redis-backed queue + worker (Section 6.2); no
in-memory scheduling lives here. Only file sending and the auto-delete notice.
"""
from __future__ import annotations

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from app.bot.messages import auto_delete_notice
from app.models.media_file import MediaFile


async def send_media_file(
    bot: Bot,
    chat_id: int,
    media_file: MediaFile,
    *,
    caption: str | None = None,
    protect_content: bool = False,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> int:
    """Send a single stored file by its Telegram file_id; return message_id."""
    file_type = media_file.file_type
    file_id = media_file.telegram_file_id
    common = {
        "chat_id": chat_id,
        "protect_content": protect_content,
        "reply_markup": reply_markup,
    }

    if file_type == "photo":
        sent = await bot.send_photo(photo=file_id, caption=caption, **common)
    elif file_type == "video":
        sent = await bot.send_video(video=file_id, caption=caption, **common)
    elif file_type == "animation":
        sent = await bot.send_animation(animation=file_id, caption=caption, **common)
    elif file_type == "audio":
        sent = await bot.send_audio(audio=file_id, caption=caption, **common)
    elif file_type == "voice":
        # Voice carries no caption.
        sent = await bot.send_voice(voice=file_id, **common)
    elif file_type == "sticker":
        # Sticker carries no caption.
        sent = await bot.send_sticker(sticker=file_id, **common)
    else:  # document + any fallback
        sent = await bot.send_document(document=file_id, caption=caption, **common)

    return sent.message_id


async def notify_auto_delete(bot: Bot, chat_id: int, seconds: int) -> None:
    """Tell the user the delivered files will be auto-deleted."""
    await bot.send_message(chat_id, auto_delete_notice(seconds))
