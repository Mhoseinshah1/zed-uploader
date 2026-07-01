"""Album (media_group_id) buffering.

Each album part is buffered in Redis under its group id; the worker finalizes the
whole group into a single Media after a short debounce (see AlbumBuffer). This
handler is registered AFTER batch (so batch-collecting still captures parts) and
BEFORE the single-file upload handler (so grouped media are not each turned into
their own Media). Single messages (no media_group_id) fall through unchanged.
"""
from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.types import Message

from app.bot.handlers.upload import MEDIA_FILTER, extract_file
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.services.album_buffer import AlbumBuffer

router = Router(name="albums")
log = get_logger("handler.albums")


@router.message(F.media_group_id, MEDIA_FILTER)
async def album_part(message: Message) -> None:
    if message.from_user is None:
        return
    extracted = extract_file(message)
    if extracted is None:
        return  # unsupported part: just skip it
    file_data, caption = extracted
    gk = AlbumBuffer.group_key(message.chat.id, str(message.media_group_id))
    await AlbumBuffer(get_redis()).add(
        gk,
        chat_id=message.chat.id,
        telegram_id=message.from_user.id,
        part={"file": file_data, "caption": caption},
        now=time.time(),
    )
