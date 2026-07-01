"""Admin panel: reply-keyboard handlers, callback handlers, and FSM inputs.

Handlers stay thin — all logic lives in MediaService / BotSettingService.
Every callback answers (dismisses the spinner) and is guarded by IsAdmin.
All media actions are owner-scoped via db_user.id.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import FilesCb, MediaCb, SetCb
from app.bot.filters import IsAdmin
from app.bot.gating import feature_allowed
from app.bot.keyboards.inline import (
    build_confirm_delete,
    build_files_list,
    build_manage,
    build_open_plans,
    build_settings,
)
from app.bot.keyboards.reply import build_admin_menu
from app.bot.states import MediaEdit, SettingsEdit
from app.core.logging import get_logger
from app.models.media import Media
from app.models.user import User
from app.services.admin_service import AdminService
from app.services.bot_setting_service import (
    KEY_AUTODELETE,
    KEY_PROTECT,
    BotSettingService,
)
from app.services.feature_service import FeatureService
from app.services.media_service import MediaService


async def _deny_feature(
    callback: CallbackQuery, session: AsyncSession, feature_key: str
) -> None:
    """Send a 'requires plan X' prompt with a link to the plans menu."""
    required = await FeatureService.required_plan(session, feature_key)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            messages.requires_plan(required), reply_markup=build_open_plans()
        )
    await callback.answer()

router = Router(name="menu")
log = get_logger("handler.menu")

PAGE_SIZE = 5


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _media_type(media: Media) -> str:
    return media.files[0].file_type if media.files else messages.UNKNOWN_TYPE


def _manage_text(media: Media) -> str:
    return messages.manage_view(
        code=media.code,
        file_type=_media_type(media),
        is_active=media.is_active,
        protect_content=media.protect_content,
        auto_delete_seconds=media.auto_delete_seconds,
        download_count=media.download_count,
        download_limit=media.download_limit,
        has_password=media.password_hash is not None,
    )


async def _safe_edit(
    message: Message, text: str, markup: InlineKeyboardMarkup | None
) -> None:
    """edit_text but ignore Telegram's 'message is not modified' error."""
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass


async def _render_files(
    session: AsyncSession, owner_id: int, page: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    service = MediaService(session)
    total = await service.count_by_owner(owner_id)
    if total == 0:
        return messages.NO_FILES, None
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    items = await service.list_by_owner(
        owner_id, limit=PAGE_SIZE, offset=page * PAGE_SIZE
    )
    return (
        messages.files_list_header(total, page + 1, total_pages),
        build_files_list(items, page, total_pages),
    )


async def _show_settings(target: Message, session: AsyncSession) -> None:
    svc = BotSettingService(session)
    protect = await svc.effective_protect()
    seconds = await svc.effective_autodelete()
    await target.answer(
        messages.settings_view(protect, seconds),
        reply_markup=build_settings(protect, seconds),
    )


# ---------------------------------------------------------------------------
# reply-keyboard buttons (admins only) — each clears any in-progress FSM flow
# ---------------------------------------------------------------------------
@router.message(IsAdmin(), Command("panel"))
async def cmd_panel(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    is_owner = message.from_user is not None and await AdminService.is_owner(
        session, message.from_user.id
    )
    await message.answer(messages.ADMIN_PANEL, reply_markup=build_admin_menu(is_owner))


@router.message(IsAdmin(), F.text == messages.BTN_UPLOAD)
async def btn_upload(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(messages.UPLOAD_PROMPT)


@router.message(IsAdmin(), F.text == messages.BTN_MY_FILES)
async def btn_my_files(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    await state.clear()
    if db_user is None:
        return
    text, markup = await _render_files(session, db_user.id, 0)
    await message.answer(text, reply_markup=markup)


@router.message(IsAdmin(), F.text == messages.BTN_STATS)
async def btn_stats(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    await state.clear()
    if db_user is None:
        return
    count, downloads = await MediaService(session).owner_stats(db_user.id)
    await message.answer(messages.owner_stats_view(count, downloads))


@router.message(IsAdmin(), F.text == messages.BTN_SETTINGS)
async def btn_settings(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    await _show_settings(message, session)


# ---------------------------------------------------------------------------
# pagination
# ---------------------------------------------------------------------------
@router.callback_query(IsAdmin(), FilesCb.filter())
async def cb_files_page(
    callback: CallbackQuery,
    callback_data: FilesCb,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    if db_user is not None and isinstance(callback.message, Message):
        text, markup = await _render_files(session, db_user.id, callback_data.page)
        await _safe_edit(callback.message, text, markup)
    await callback.answer()


# ---------------------------------------------------------------------------
# per-file management
# ---------------------------------------------------------------------------
@router.callback_query(IsAdmin(), MediaCb.filter())
async def cb_media(
    callback: CallbackQuery,
    callback_data: MediaCb,
    session: AsyncSession,
    db_user: User | None,
    state: FSMContext,
) -> None:
    if db_user is None or not isinstance(callback.message, Message):
        await callback.answer()
        return

    service = MediaService(session)
    action, mid, page = callback_data.action, callback_data.id, callback_data.page
    media = await service.get_owned(mid, db_user.id)
    if media is None:
        await callback.answer(messages.NOT_OWNED, show_alert=True)
        return

    if action == "manage":
        await _safe_edit(callback.message, _manage_text(media), build_manage(media, page))
        await callback.answer()

    elif action == "toggle_active":
        await service.set_active(mid, db_user.id, not media.is_active)
        log.info("media_updated", id=mid, field="is_active")
        media = await service.get_owned(mid, db_user.id)
        await _safe_edit(callback.message, _manage_text(media), build_manage(media, page))
        await callback.answer(messages.ACTIVE_SET)

    elif action == "toggle_protect":
        # Turning protection ON is gated; turning it OFF is always allowed.
        tg_id = callback.from_user.id if callback.from_user else 0
        if not media.protect_content and not await feature_allowed(
            session, "protect_content", db_user, tg_id
        ):
            await _deny_feature(callback, session, "protect_content")
            return
        await service.set_protect(mid, db_user.id, not media.protect_content)
        log.info("media_updated", id=mid, field="protect_content")
        media = await service.get_owned(mid, db_user.id)
        await _safe_edit(callback.message, _manage_text(media), build_manage(media, page))
        await callback.answer(messages.PROTECT_SET)

    elif action == "autodel":
        tg_id = callback.from_user.id if callback.from_user else 0
        if not await feature_allowed(session, "auto_delete", db_user, tg_id):
            await _deny_feature(callback, session, "auto_delete")
            return
        await state.set_state(MediaEdit.waiting_autodelete)
        await state.update_data(media_id=mid, page=page)
        await callback.message.answer(messages.ASK_AUTODELETE)
        await callback.answer()

    elif action == "setlimit":
        await state.set_state(MediaEdit.waiting_limit)
        await state.update_data(media_id=mid, page=page)
        await callback.message.answer(messages.ASK_LIMIT)
        await callback.answer()

    elif action == "editcap":
        await state.set_state(MediaEdit.waiting_caption)
        await state.update_data(media_id=mid, page=page)
        await callback.message.answer(messages.ASK_CAPTION)
        await callback.answer()

    elif action == "setpw":
        await state.set_state(MediaEdit.waiting_password)
        await state.update_data(media_id=mid, page=page)
        await callback.message.answer(messages.ASK_MEDIA_PASSWORD)
        await callback.answer()

    elif action == "movefolder":
        from app.bot.keyboards.inline import build_folder_picker
        from app.services.folder_service import FolderService

        await state.set_state(MediaEdit.waiting_folder)
        await state.update_data(media_id=mid, page=page)
        folders = await FolderService(session).list_all()
        await callback.message.answer(
            messages.CHOOSE_TARGET_FOLDER, reply_markup=build_folder_picker(folders)
        )
        await callback.answer()

    elif action == "link":
        await callback.answer()
        await callback.message.answer(messages.share_link(service.deep_link(media)))

    elif action == "stats":
        await callback.answer()
        await callback.message.answer(
            messages.file_stats_view(
                media.code, media.download_count, media.download_limit
            )
        )

    elif action == "del":
        try:
            await callback.message.edit_reply_markup(
                reply_markup=build_confirm_delete(mid, page)
            )
        except TelegramBadRequest:
            pass
        await callback.answer()

    elif action == "delok":
        await service.delete_media(mid, db_user.id)
        log.info("media_deleted", id=mid)
        text, markup = await _render_files(session, db_user.id, page)
        await _safe_edit(callback.message, text, markup)
        await callback.answer(messages.DELETED)

    elif action == "back":
        text, markup = await _render_files(session, db_user.id, page)
        await _safe_edit(callback.message, text, markup)
        await callback.answer()

    else:
        await callback.answer()


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
@router.callback_query(IsAdmin(), SetCb.filter())
async def cb_settings(
    callback: CallbackQuery,
    callback_data: SetCb,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    svc = BotSettingService(session)

    if callback_data.action == "protect":
        current = await svc.effective_protect()
        await svc.set(KEY_PROTECT, not current)
        log.info("setting_updated", key=KEY_PROTECT)
        protect = await svc.effective_protect()
        seconds = await svc.effective_autodelete()
        await _safe_edit(
            callback.message,
            messages.settings_view(protect, seconds),
            build_settings(protect, seconds),
        )
        await callback.answer(messages.SETTINGS_SAVED)

    elif callback_data.action == "autodel":
        await state.set_state(SettingsEdit.waiting_default_autodelete)
        await callback.message.answer(messages.ASK_AUTODELETE)
        await callback.answer()

    else:
        await callback.answer()


# ---------------------------------------------------------------------------
# FSM text inputs (admins only). Registered after the button handlers, so an
# exact button text still routes to its button handler (which clears state).
# ---------------------------------------------------------------------------
async def _reshow_manage(
    message: Message, service: MediaService, owner_id: int, media_id: int, page: int
) -> None:
    media = await service.get_owned(media_id, owner_id)
    if media is not None:
        await message.answer(_manage_text(media), reply_markup=build_manage(media, page))


@router.message(IsAdmin(), StateFilter(MediaEdit.waiting_limit), F.text)
async def input_limit(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    raw = (message.text or "").strip()
    value = None if raw == "0" else raw
    if value is not None and not value.isdigit():
        await message.answer(messages.INVALID_NUMBER)
        return
    limit = int(value) if value is not None else None
    data = await state.get_data()
    await state.clear()
    if db_user is None:
        return
    service = MediaService(session)
    mid, page = int(data["media_id"]), int(data.get("page", 0))
    if not await service.set_download_limit(mid, db_user.id, limit):
        await message.answer(messages.NOT_OWNED)
        return
    log.info("media_updated", id=mid, field="download_limit")
    await message.answer(messages.LIMIT_SET)
    await _reshow_manage(message, service, db_user.id, mid, page)


@router.message(IsAdmin(), StateFilter(MediaEdit.waiting_autodelete), F.text)
async def input_autodelete(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer(messages.INVALID_NUMBER)
        return
    seconds = int(raw)
    data = await state.get_data()
    await state.clear()
    if db_user is None:
        return
    service = MediaService(session)
    mid, page = int(data["media_id"]), int(data.get("page", 0))
    if not await service.set_auto_delete(mid, db_user.id, seconds or None):
        await message.answer(messages.NOT_OWNED)
        return
    log.info("media_updated", id=mid, field="auto_delete_seconds")
    await message.answer(messages.AUTODELETE_SET)
    await _reshow_manage(message, service, db_user.id, mid, page)


@router.message(IsAdmin(), StateFilter(MediaEdit.waiting_caption), F.text)
async def input_caption(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    raw = message.text or ""
    caption = None if raw.strip() == "-" else raw
    data = await state.get_data()
    await state.clear()
    if db_user is None:
        return
    service = MediaService(session)
    mid, page = int(data["media_id"]), int(data.get("page", 0))
    if not await service.set_caption(mid, db_user.id, caption):
        await message.answer(messages.NOT_OWNED)
        return
    log.info("media_updated", id=mid, field="caption")
    await message.answer(messages.CAPTION_SET)
    await _reshow_manage(message, service, db_user.id, mid, page)


@router.message(IsAdmin(), StateFilter(MediaEdit.waiting_password), F.text)
async def input_media_password(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    raw = (message.text or "").strip()
    if not raw:
        await message.answer(messages.ASK_MEDIA_PASSWORD)  # re-prompt, keep state
        return
    data = await state.get_data()
    await state.clear()
    if db_user is None:
        return
    service = MediaService(session)
    mid, page = int(data["media_id"]), int(data.get("page", 0))
    if raw == "-":
        ok = await service.clear_password(mid, db_user.id)
        confirmation = messages.MEDIA_PASSWORD_REMOVED
    else:
        ok = await service.set_password(mid, db_user.id, raw)
        confirmation = messages.MEDIA_PASSWORD_SET
    if not ok:
        await message.answer(messages.NOT_OWNED)
        return
    log.info("media_updated", id=mid, field="password_hash")
    await message.answer(confirmation)
    await _reshow_manage(message, service, db_user.id, mid, page)


@router.message(IsAdmin(), StateFilter(SettingsEdit.waiting_default_autodelete), F.text)
async def input_default_autodelete(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer(messages.INVALID_NUMBER)
        return
    await state.clear()
    svc = BotSettingService(session)
    await svc.set(KEY_AUTODELETE, int(raw))
    log.info("setting_updated", key=KEY_AUTODELETE)
    await message.answer(messages.SETTINGS_SAVED)
    await _show_settings(message, session)
