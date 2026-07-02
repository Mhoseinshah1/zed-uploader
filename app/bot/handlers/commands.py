"""Slash-command aliases for the Telegram Menu button.

Every command advertised by the scope-based menu (bot_command_service) has a
handler here or earlier (/start in start.py, /panel in menu.py): the aliases
below call the existing handlers directly — no duplicated logic. Registered
right after `start` so a slash command preempts any in-progress FSM text
handler (same convention as the reply buttons: menu actions cancel flows);
/search is aliased here for that reason too, since search.router is last.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.filters import IsAdmin, IsOwner
from app.bot.handlers.ads import ads_menu
from app.bot.handlers.billing import subscription_menu, wallet_menu
from app.bot.handlers.broadcast import broadcast_start
from app.bot.handlers.folders import folders_menu
from app.bot.handlers.menu import _render_files, btn_stats, btn_upload
from app.bot.handlers.review import review_menu
from app.bot.handlers.search import search_command
from app.models.user import User
from app.services.admin_service import AdminService
from app.services.media_service import MediaService
from app.services.text_service import get_text

router = Router(name="commands")


@router.message(Command("help"))
async def cmd_help(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    await message.answer(await get_text(session, "help"))


@router.message(Command("buy"))
async def cmd_buy(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    await subscription_menu(message, state, session, db_user)


@router.message(Command("wallet"))
async def cmd_wallet(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    await wallet_menu(message, state, session, db_user)


@router.message(Command("myfiles"))
async def cmd_myfiles(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    """Files the caller owns — any user (user uploads may be enabled).

    Admins get the interactive manage/paginate keyboard; regular users get a
    plain link list, because those callbacks are IsAdmin-gated (the buttons
    would be dead for them).
    """
    await state.clear()
    if db_user is None:
        return
    tg_id = message.from_user.id if message.from_user else 0
    if await AdminService.is_admin(session, tg_id):
        text, markup = await _render_files(session, db_user.id, 0)
        await message.answer(text, reply_markup=markup)
        return
    service = MediaService(session)
    items = await service.list_by_owner(db_user.id, limit=20)
    if not items:
        await message.answer(messages.NO_FILES)
        return
    lines = [f"• {media.code}\n{service.deep_link(media)}" for media in items]
    await message.answer(messages.MY_FILES_HEADER + "\n\n" + "\n".join(lines))


@router.message(Command("search"))
async def cmd_search(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    await search_command(message, command, state, session, db_user)


@router.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(messages.REPORT_HELP)


@router.message(IsAdmin(), Command("upload"))
async def cmd_upload(message: Message, state: FSMContext) -> None:
    await btn_upload(message, state)


@router.message(IsAdmin(), Command("stats"))
async def cmd_stats(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    await btn_stats(message, state, session, db_user)


@router.message(IsAdmin(), Command("review"))
async def cmd_review(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await review_menu(message, state, session)


@router.message(IsAdmin(), Command("folders"))
async def cmd_folders(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await folders_menu(message, state, session)


@router.message(IsOwner(), Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    await broadcast_start(message, state)


@router.message(IsOwner(), Command("ads"))
async def cmd_ads(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await ads_menu(message, state, session)


@router.message(IsOwner(), Command("backup"))
async def cmd_backup(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(messages.BACKUP_POINTER)
