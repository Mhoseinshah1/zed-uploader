"""Reactions + favorites + sorted public views (J1).

Reaction buttons live under every delivered file (see delivery.py); toggling
edits the markup in place with fresh counters. «⭐ علاقه‌مندی‌ها» opens the
user's own favorites; the public sorted views (popular / newest / most-viewed)
are part of the public-browse surface, so they respect the same
``public_search_enabled`` switch as B3 search. Listings show only approved +
active media of the CURRENT tenant (service-enforced).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import BrowseCb, ReactCb
from app.bot.keyboards.inline import build_delivered_actions
from app.core.logging import get_logger
from app.models.media import Media
from app.models.user import User
from app.services.bot_setting_service import BotSettingService
from app.services.media_service import MediaService
from app.services.reaction_service import ReactionService

router = Router(name="reactions")
log = get_logger("handler.reactions")

PAGE = 10
_PUBLIC_SORTS = ("popular", "newest", "most_viewed")


@router.callback_query(ReactCb.filter())
async def react_toggle(
    callback: CallbackQuery,
    callback_data: ReactCb,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    if db_user is None:
        await callback.answer()
        return
    svc = ReactionService(session)
    now_set = await svc.toggle(callback_data.id, db_user.id, callback_data.kind)
    media_svc = MediaService(session)
    media = await session.get(Media, callback_data.id)
    if media is not None and isinstance(callback.message, Message):
        markup = build_delivered_actions(
            await media_svc.deep_link(media), media.id,
            likes=media.like_count, dislikes=media.dislike_count,
        )
        try:
            await callback.message.edit_reply_markup(reply_markup=markup)
        except TelegramBadRequest:
            pass  # markup unchanged — fine
    notice = (
        messages.REACT_SET if now_set else messages.REACT_CLEARED
    ).get(callback_data.kind, "✅")
    await callback.answer(notice)


def _chips(active: str) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=("• " if active == s else "") + messages._BROWSE_TITLES[s].split(" ")[0],
            callback_data=BrowseCb(sort=s, page=0).pack(),
        )
        for s in ("favs",) + _PUBLIC_SORTS
    ]


async def _render_listing(
    session: AsyncSession, db_user: User, sort: str, page: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    svc = ReactionService(session)
    media_svc = MediaService(session)
    if sort == "favs":
        rows = await svc.favorites(db_user.id, limit=PAGE, offset=page * PAGE)
    else:
        rows = await svc.listing(sort, limit=PAGE, offset=page * PAGE)
    if not rows and page == 0 and sort == "favs":
        return messages.FAVORITES_EMPTY, InlineKeyboardMarkup(
            inline_keyboard=[_chips(sort)]
        )
    keyboard: list[list[InlineKeyboardButton]] = [_chips(sort)]
    for m in rows:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=messages.browse_row(m.code, m.title, m.like_count, m.download_count),
                    url=await media_svc.deep_link(m),
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=messages.LBL_PREV, callback_data=BrowseCb(sort=sort, page=page - 1).pack()
            )
        )
    if len(rows) == PAGE:
        nav.append(
            InlineKeyboardButton(
                text=messages.LBL_NEXT, callback_data=BrowseCb(sort=sort, page=page + 1).pack()
            )
        )
    if nav:
        keyboard.append(nav)
    return messages.browse_header(sort, page), InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.message(F.text == messages.BTN_FAVORITES)
async def favorites_menu(
    message: Message, session: AsyncSession, db_user: User | None
) -> None:
    if db_user is None:
        return
    text, markup = await _render_listing(session, db_user, "favs", 0)
    await message.answer(text, reply_markup=markup)


@router.callback_query(BrowseCb.filter())
async def browse_page(
    callback: CallbackQuery,
    callback_data: BrowseCb,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    if db_user is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    sort, page = callback_data.sort, max(0, callback_data.page)
    if sort in _PUBLIC_SORTS and not await BotSettingService(session).public_search_enabled():
        await callback.answer(messages.BROWSE_DISABLED, show_alert=True)
        return
    text, markup = await _render_listing(session, db_user, sort, page)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass
    await callback.answer()
