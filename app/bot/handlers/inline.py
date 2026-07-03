"""Telegram inline search (J2): ``@bot <query>``.

Returns ONLY this tenant's approved + active media (reuses the ILIKE-escaped,
bounded ``MediaService.search``) as inline articles whose message is the file's
deep link — inline results are a public sharing surface, so pending/rejected/
other-tenant media can never appear. Non-admin access respects the same
``public_search_enabled`` switch as B3 chat search; admins may always search
(still approved-only here). Paginated via ``next_offset``; cached briefly.

The tenant context comes from the webhook that delivered the update (the bot
token identifies the tenant), exactly like every other update type.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.core.logging import get_logger
from app.models.user import User
from app.services.admin_service import AdminService
from app.services.bot_setting_service import BotSettingService
from app.services.media_service import MediaService

router = Router(name="inline")
log = get_logger("handler.inline")

PAGE = 20  # Telegram allows up to 50 results per answer
CACHE_SECONDS = 30


@router.inline_query()
async def inline_search(
    query: InlineQuery, session: AsyncSession, db_user: User | None
) -> None:
    text = (query.query or "").strip()
    offset = int(query.offset) if (query.offset or "").isdigit() else 0

    is_admin = await AdminService.is_admin(session, query.from_user.id)
    if not is_admin and not await BotSettingService(session).public_search_enabled():
        await query.answer([], cache_time=CACHE_SECONDS, is_personal=True)
        return
    if not text:
        await query.answer([], cache_time=CACHE_SECONDS, is_personal=True)
        return

    service = MediaService(session)
    items, total = await service.search(
        text, approved_only=True, limit=PAGE, offset=offset
    )
    results = []
    for m in items:
        link = await service.deep_link(m)
        results.append(
            InlineQueryResultArticle(
                id=f"m{m.id}",
                title=m.title or m.code,
                description=messages.inline_result_description(
                    m.download_count, m.like_count
                ),
                input_message_content=InputTextMessageContent(
                    message_text=messages.inline_result_message(
                        m.title or m.code, link
                    )
                ),
            )
        )
    next_offset = str(offset + PAGE) if offset + PAGE < total else ""
    await query.answer(
        results, cache_time=CACHE_SECONDS, is_personal=True, next_offset=next_offset
    )
