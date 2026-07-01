"""Folder management (admins): browse, create, rename, delete, and move media.

Folder browsing shows the admin's OWN media in a folder (owner-scoped), matching
the "my files" view; the panel offers the full cross-owner view.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import FolderCb, FolderPickCb
from app.bot.filters import IsAdmin
from app.bot.keyboards.inline import (
    build_confirm_folder_delete,
    build_folder_picker,
    build_folder_view,
    build_folders_root,
)
from app.bot.states import FolderEdit, MediaEdit
from app.core.logging import get_logger
from app.models.user import User
from app.services.folder_service import DELETE_HAS_CHILDREN, DELETE_OK, FolderService
from app.services.media_service import MediaService

router = Router(name="folders")
log = get_logger("handler.folders")

PAGE_SIZE = 5


async def _render_root(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    folders = await FolderService(session).list_children(None)
    header = messages.FOLDERS_ROOT_HEADER if folders else messages.FOLDERS_EMPTY
    return header, build_folders_root(folders)


async def _render_folder(
    session: AsyncSession, folder_id: int, owner_id: int, page: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    fsvc = FolderService(session)
    folder = await fsvc.get(folder_id)
    if folder is None:
        return None
    subfolders = await fsvc.list_children(folder_id)
    msvc = MediaService(session)
    total = await msvc.count_by_folder(folder_id, owner_id)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    media_items = await msvc.list_by_folder(
        folder_id, owner_id, limit=PAGE_SIZE, offset=page * PAGE_SIZE
    )
    text = messages.folder_view_header(folder.name, len(subfolders), total)
    return text, build_folder_view(folder, subfolders, media_items, page, total_pages)


async def _safe_edit(message: Message, text: str, markup) -> None:
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass


@router.message(IsAdmin(), F.text == messages.BTN_FOLDERS)
async def folders_menu(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    text, markup = await _render_root(session)
    await message.answer(text, reply_markup=markup)


@router.callback_query(IsAdmin(), FolderCb.filter(F.action == "root"))
async def folder_root(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if isinstance(callback.message, Message):
        text, markup = await _render_root(session)
        await _safe_edit(callback.message, text, markup)
    await callback.answer()


@router.callback_query(IsAdmin(), FolderCb.filter(F.action == "open"))
async def folder_open(
    callback: CallbackQuery,
    callback_data: FolderCb,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    if db_user is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    if callback_data.id == 0:
        text, markup = await _render_root(session)
        await _safe_edit(callback.message, text, markup)
        await callback.answer()
        return
    rendered = await _render_folder(session, callback_data.id, db_user.id, callback_data.page)
    if rendered is None:
        await callback.answer(messages.FOLDER_GONE, show_alert=True)
        return
    await _safe_edit(callback.message, *rendered)
    await callback.answer()


@router.callback_query(IsAdmin(), FolderCb.filter(F.action == "new"))
async def folder_new(
    callback: CallbackQuery,
    callback_data: FolderCb,
    state: FSMContext,
) -> None:
    await state.set_state(FolderEdit.waiting_name)
    await state.update_data(parent_id=callback_data.id or None)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.ASK_FOLDER_NAME)
    await callback.answer()


@router.callback_query(IsAdmin(), FolderCb.filter(F.action == "rename"))
async def folder_rename_prompt(
    callback: CallbackQuery,
    callback_data: FolderCb,
    state: FSMContext,
) -> None:
    await state.set_state(FolderEdit.waiting_rename)
    await state.update_data(folder_id=callback_data.id)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.ASK_FOLDER_RENAME)
    await callback.answer()


@router.callback_query(IsAdmin(), FolderCb.filter(F.action == "del"))
async def folder_delete_confirm(
    callback: CallbackQuery, callback_data: FolderCb
) -> None:
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(
                reply_markup=build_confirm_folder_delete(callback_data.id)
            )
        except TelegramBadRequest:
            pass
    await callback.answer()


@router.callback_query(IsAdmin(), FolderCb.filter(F.action == "delok"))
async def folder_delete(
    callback: CallbackQuery, callback_data: FolderCb, session: AsyncSession
) -> None:
    result = await FolderService(session).delete(callback_data.id)
    if result == DELETE_HAS_CHILDREN:
        await callback.answer(messages.FOLDER_HAS_CHILDREN, show_alert=True)
        return
    log.info("folder_deleted", folder_id=callback_data.id, result=result)
    if isinstance(callback.message, Message):
        text, markup = await _render_root(session)
        await _safe_edit(callback.message, text, markup)
    await callback.answer(
        messages.FOLDER_DELETED if result == DELETE_OK else messages.FOLDER_GONE
    )


@router.message(IsAdmin(), FolderEdit.waiting_name, F.text)
async def folder_name_input(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer(messages.ASK_FOLDER_NAME)
        return
    data = await state.get_data()
    await state.clear()
    parent_id = data.get("parent_id")
    tg_id = message.from_user.id if message.from_user else None
    folder = await FolderService(session).create(
        name, parent_id=parent_id, owner_admin_id=tg_id
    )
    if folder is None:
        await message.answer(messages.FOLDER_GONE)
        return
    log.info("folder_created", folder_id=folder.id, parent_id=parent_id)
    await message.answer(messages.FOLDER_CREATED)
    text, markup = await _render_root(session)
    await message.answer(text, reply_markup=markup)


@router.message(IsAdmin(), FolderEdit.waiting_rename, F.text)
async def folder_rename_input(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer(messages.ASK_FOLDER_RENAME)
        return
    data = await state.get_data()
    await state.clear()
    folder_id = int(data["folder_id"])
    if not await FolderService(session).rename(folder_id, name):
        await message.answer(messages.FOLDER_GONE)
        return
    log.info("folder_renamed", folder_id=folder_id)
    await message.answer(messages.FOLDER_RENAMED)


# --- move a media into a folder (invoked from the file manage view) ---------
@router.callback_query(IsAdmin(), FolderPickCb.filter())
async def folder_pick(
    callback: CallbackQuery,
    callback_data: FolderPickCb,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    current = await state.get_state()
    if current != MediaEdit.waiting_folder.state or db_user is None:
        await callback.answer()
        return
    data = await state.get_data()
    await state.clear()
    media_id = int(data["media_id"])
    folder_id = callback_data.id or None
    ok = await MediaService(session).set_folder(media_id, db_user.id, folder_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            messages.MEDIA_MOVED if ok else messages.NOT_OWNED
        )
    await callback.answer()
