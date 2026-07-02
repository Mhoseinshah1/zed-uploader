"""Owner ad management (bot): list with counts, create, toggle, delete.

Field-level editing (button/target plan/impression limit) lives in the web
panel; the bot covers the quick operations.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import AdCb
from app.bot.filters import IsOwner
from app.bot.states import AdCreate
from app.core.logging import get_logger
from app.models.ad import PLACEMENTS
from app.services.ad_service import AdService

router = Router(name="ads")
log = get_logger("handler.ads")


def _build_ads_list(ads) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for ad in ads:
        b.row(
            InlineKeyboardButton(
                text=messages.ad_row_label(
                    ad.id, ad.title, ad.placement, ad.is_active,
                    ad.impression_count, ad.click_count,
                ),
                callback_data=AdCb(action="toggle", id=ad.id).pack(),
            ),
            InlineKeyboardButton(
                text=messages.LBL_DELETE,
                callback_data=AdCb(action="del", id=ad.id).pack(),
            ),
        )
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_NEW_AD, callback_data=AdCb(action="new").pack()
        )
    )
    return b.as_markup()


def _build_placement_pick() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key in PLACEMENTS:
        b.row(
            InlineKeyboardButton(
                text=messages.placement_title(key),
                callback_data=AdCb(action="place", value=key).pack(),
            )
        )
    return b.as_markup()


async def _render_list(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    ads = await AdService(session).list_all()
    header = messages.ADS_HEADER if ads else messages.ADS_EMPTY
    return header, _build_ads_list(ads)


@router.message(IsOwner(), F.text == messages.BTN_ADS)
async def ads_menu(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    text, markup = await _render_list(session)
    await message.answer(text, reply_markup=markup)


@router.callback_query(IsOwner(), AdCb.filter(F.action == "toggle"))
async def ad_toggle(
    callback: CallbackQuery, callback_data: AdCb, session: AsyncSession
) -> None:
    ok = await AdService(session).toggle(callback_data.id)
    if isinstance(callback.message, Message):
        text, markup = await _render_list(session)
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            pass
    await callback.answer(messages.AD_TOGGLED if ok else messages.AD_GONE)


@router.callback_query(IsOwner(), AdCb.filter(F.action == "del"))
async def ad_delete(
    callback: CallbackQuery, callback_data: AdCb, session: AsyncSession
) -> None:
    ok = await AdService(session).delete(callback_data.id)
    log.info("ad_deleted", ad_id=callback_data.id, ok=ok)
    if isinstance(callback.message, Message):
        text, markup = await _render_list(session)
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            pass
    await callback.answer(messages.AD_DELETED if ok else messages.AD_GONE)


@router.callback_query(IsOwner(), AdCb.filter(F.action == "new"))
async def ad_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdCreate.waiting_title)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.ASK_AD_TITLE)
    await callback.answer()


@router.message(IsOwner(), AdCreate.waiting_title, F.text)
async def ad_title_input(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer(messages.ASK_AD_TITLE)
        return
    await state.update_data(title=title)
    await state.set_state(AdCreate.waiting_text)
    await message.answer(messages.ASK_AD_TEXT)


@router.message(IsOwner(), AdCreate.waiting_text, F.text)
async def ad_text_input(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer(messages.ASK_AD_TEXT)
        return
    await state.update_data(text=text)
    await state.set_state(AdCreate.waiting_placement)
    await message.answer(messages.ASK_AD_PLACEMENT, reply_markup=_build_placement_pick())


@router.callback_query(IsOwner(), AdCreate.waiting_placement, AdCb.filter(F.action == "place"))
async def ad_placement_pick(
    callback: CallbackQuery,
    callback_data: AdCb,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if callback_data.value not in PLACEMENTS:
        await callback.answer()
        return
    data = await state.get_data()
    await state.clear()
    ad = await AdService(session).create(
        title=str(data.get("title", "")),
        text=str(data.get("text", "")),
        placement=callback_data.value,
    )
    log.info("ad_created", ad_id=ad.id, placement=ad.placement)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.AD_CREATED)
        text, markup = await _render_list(session)
        await callback.message.answer(text, reply_markup=markup)
    await callback.answer()
