"""/start handler.

Implements the exact deep-link flow from Section 7:
1. try_claim_download(code)
2. on NOT_FOUND / INACTIVE / LIMIT_REACHED -> Persian message + return
3. send every MediaFile (caption on first only; protect_content from media)
4. if nothing sent -> release_download + generic error
5. log_download
6. if auto_delete_seconds > 0 -> notify + schedule in the Redis queue
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.keyboards.inline import build_share
from app.bot.keyboards.reply import build_admin_menu
from app.bot.sender import notify_auto_delete, send_media_file
from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.models.user import User
from app.services.autodelete import AutoDeleteQueue
from app.services.media_service import MediaService, MediaStatus

router = Router(name="start")
log = get_logger("handler.start")

_STATUS_MESSAGES = {
    MediaStatus.NOT_FOUND: messages.NOT_FOUND,
    MediaStatus.INACTIVE: messages.INACTIVE,
    MediaStatus.LIMIT_REACHED: messages.LIMIT_REACHED,
}


async def _send_welcome(message: Message) -> None:
    """Welcome; admins also get the persistent reply keyboard."""
    user = message.from_user
    if user is not None and user.id in settings.admin_id_list:
        await message.answer(messages.WELCOME, reply_markup=build_admin_menu())
    else:
        await message.answer(messages.WELCOME)


@router.message(CommandStart(deep_link=True))
async def start_with_code(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    code = (command.args or "").strip()
    if not code:
        await _send_welcome(message)
        return

    service = MediaService(session)

    # 1. atomic claim
    status, media = await service.try_claim_download(code)

    # 2. failure paths
    if status is not MediaStatus.OK or media is None:
        await message.answer(_STATUS_MESSAGES.get(status, messages.NOT_FOUND))
        return

    # 3. send every related file (caption + share button on the first only)
    share_markup = build_share(service.deep_link(media))
    sent_ids: list[int] = []
    for index, media_file in enumerate(media.files):
        caption = media.caption if index == 0 else None
        reply_markup = share_markup if index == 0 else None
        try:
            message_id = await send_media_file(
                message.bot,
                message.chat.id,
                media_file,
                caption=caption,
                protect_content=media.protect_content,
                reply_markup=reply_markup,
            )
            sent_ids.append(message_id)
        except Exception as exc:  # keep going; a failed item shouldn't abort all
            log.warning("send_failed", media_id=media.id, error=str(exc))

    # 4. nothing delivered -> release the claimed slot + generic error
    if not sent_ids:
        await service.release_download(media.id)
        await message.answer(messages.GENERIC_ERROR)
        return

    # 5. log the successful download
    await service.log_download(
        media.id,
        telegram_id=message.from_user.id,
        user_id=db_user.id if db_user else None,
    )

    # 6. schedule auto-delete via the persistent Redis queue
    if media.auto_delete_seconds and media.auto_delete_seconds > 0:
        await notify_auto_delete(message, media.auto_delete_seconds)
        await AutoDeleteQueue(get_redis()).schedule(
            message.chat.id, sent_ids, media.auto_delete_seconds
        )


@router.message(CommandStart())
async def start_plain(message: Message, db_user: User | None) -> None:
    await _send_welcome(message)
