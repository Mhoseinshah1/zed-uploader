"""In-bot file search.

Admins search their OWN media (any status); everyone else can search only if the
owner enabled public search, and then only over approved + active media. The
query is held in FSM state so result pages can be navigated without stuffing it
into callback data.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import SearchCb
from app.bot.filters import IsAdmin
from app.bot.keyboards.inline import build_search_results
from app.bot.states import Search
from app.core.logging import get_logger
from app.models.user import User
from app.services.admin_service import AdminService
from app.services.bot_setting_service import BotSettingService
from app.services.media_service import MediaService

router = Router(name="search")
log = get_logger("handler.search")

PAGE_SIZE = 5


async def _run_search(
    session: AsyncSession, query: str, db_user: User | None, telegram_id: int, page: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    is_admin = await AdminService.is_admin(session, telegram_id)
    if is_admin:
        owner_id = db_user.id if db_user else None
        items, total = await MediaService(session).search(
            query, owner_user_id=owner_id, limit=PAGE_SIZE, offset=page * PAGE_SIZE
        )
    else:
        items, total = await MediaService(session).search(
            query, approved_only=True, limit=PAGE_SIZE, offset=page * PAGE_SIZE
        )
    if total == 0:
        return messages.SEARCH_EMPTY, None
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return (
        messages.search_results_header(total, page + 1, total_pages),
        build_search_results(items, page, total_pages),
    )


async def _allowed(session: AsyncSession, telegram_id: int) -> bool:
    if await AdminService.is_admin(session, telegram_id):
        return True
    return await BotSettingService(session).public_search_enabled()


@router.message(Command("search"))
async def search_command(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    tg_id = message.from_user.id if message.from_user else 0
    if not await _allowed(session, tg_id):
        await message.answer(messages.SEARCH_DISABLED)
        return
    query = (command.args or "").strip()
    if not query:
        await state.set_state(Search.active)
        await state.update_data(query="")
        await message.answer(messages.ASK_SEARCH_QUERY)
        return
    await state.set_state(Search.active)
    await state.update_data(query=query)
    text, markup = await _run_search(session, query, db_user, tg_id, 0)
    await message.answer(text, reply_markup=markup)


@router.message(IsAdmin(), F.text == messages.BTN_SEARCH)
async def search_button(
    message: Message, state: FSMContext
) -> None:
    await state.set_state(Search.active)
    await state.update_data(query="")
    await message.answer(messages.ASK_SEARCH_QUERY)


@router.message(Search.active, F.text)
async def search_query_input(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer(messages.ASK_SEARCH_QUERY)
        return
    tg_id = message.from_user.id if message.from_user else 0
    if not await _allowed(session, tg_id):
        await state.clear()
        await message.answer(messages.SEARCH_DISABLED)
        return
    await state.update_data(query=query)
    text, markup = await _run_search(session, query, db_user, tg_id, 0)
    await message.answer(text, reply_markup=markup)


@router.callback_query(Search.active, SearchCb.filter())
async def search_page(
    callback: CallbackQuery,
    callback_data: SearchCb,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    data = await state.get_data()
    query = (data.get("query") or "").strip()
    if not query or not isinstance(callback.message, Message):
        await callback.answer()
        return
    tg_id = callback.from_user.id if callback.from_user else 0
    text, markup = await _run_search(session, query, db_user, tg_id, callback_data.page)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass
    await callback.answer()
