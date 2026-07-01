"""Upload handlers.

Admins uploading a supported media type get it stored and receive a deep link.
Everyone else gets the Persian "admins only" message.
"""
from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.filters import IsAdmin
from app.core.logging import get_logger
from app.models.user import User
from app.services.media_service import MediaService

router = Router(name="upload")
log = get_logger("handler.upload")

# Any of these attributes present => it's an upload we accept.
MEDIA_FILTER = (
    F.photo
    | F.animation
    | F.video
    | F.audio
    | F.voice
    | F.sticker
    | F.document
)


def extract_file(message: Message) -> tuple[dict[str, Any], str | None] | None:
    """Return (file metadata dict, caption) or None if unsupported.

    ``animation`` is checked before ``video``/``document`` because Telegram sends
    GIFs with several of these attributes populated at once.
    """
    caption = message.caption

    if message.photo:
        photo = message.photo[-1]  # largest size
        return (
            {
                "telegram_file_id": photo.file_id,
                "telegram_file_unique_id": photo.file_unique_id,
                "file_type": "photo",
                "file_size": photo.file_size,
            },
            caption,
        )

    if message.animation:
        item = message.animation
        return (
            {
                "telegram_file_id": item.file_id,
                "telegram_file_unique_id": item.file_unique_id,
                "file_type": "animation",
                "file_name": item.file_name,
                "mime_type": item.mime_type,
                "file_size": item.file_size,
            },
            caption,
        )

    if message.video:
        item = message.video
        return (
            {
                "telegram_file_id": item.file_id,
                "telegram_file_unique_id": item.file_unique_id,
                "file_type": "video",
                "file_name": item.file_name,
                "mime_type": item.mime_type,
                "file_size": item.file_size,
            },
            caption,
        )

    if message.audio:
        item = message.audio
        return (
            {
                "telegram_file_id": item.file_id,
                "telegram_file_unique_id": item.file_unique_id,
                "file_type": "audio",
                "file_name": item.file_name,
                "mime_type": item.mime_type,
                "file_size": item.file_size,
            },
            caption,
        )

    if message.voice:
        item = message.voice
        return (
            {
                "telegram_file_id": item.file_id,
                "telegram_file_unique_id": item.file_unique_id,
                "file_type": "voice",
                "mime_type": item.mime_type,
                "file_size": item.file_size,
            },
            None,  # voice carries no caption
        )

    if message.sticker:
        item = message.sticker
        return (
            {
                "telegram_file_id": item.file_id,
                "telegram_file_unique_id": item.file_unique_id,
                "file_type": "sticker",
                "file_size": item.file_size,
            },
            None,  # sticker carries no caption
        )

    if message.document:
        item = message.document
        return (
            {
                "telegram_file_id": item.file_id,
                "telegram_file_unique_id": item.file_unique_id,
                "file_type": "document",
                "file_name": item.file_name,
                "mime_type": item.mime_type,
                "file_size": item.file_size,
            },
            caption,
        )

    return None


@router.message(IsAdmin(), MEDIA_FILTER)
async def admin_upload(
    message: Message, session: AsyncSession, db_user: User | None
) -> None:
    extracted = extract_file(message)
    if extracted is None:
        await message.answer(messages.UNSUPPORTED_UPLOAD)
        return

    file_data, caption = extracted
    service = MediaService(session)
    media = await service.create_media(
        files=[file_data],
        owner_user_id=db_user.id if db_user else None,
        caption=caption,
    )
    log.info("media_created", media_id=media.id, code=media.code)
    await message.answer(messages.upload_success(service.deep_link(media), media.code))


@router.message(MEDIA_FILTER)
async def non_admin_upload(message: Message) -> None:
    await message.answer(messages.NOT_ADMIN_UPLOAD)
