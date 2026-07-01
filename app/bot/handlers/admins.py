"""In-bot admin management (owners only)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import AdminCb
from app.bot.filters import IsOwner
from app.bot.keyboards.inline import build_admins_list
from app.bot.states import AdminAdd
from app.core.logging import get_logger
from app.services.admin_service import AdminService

router = Router(name="admins")
log = get_logger("handler.admins")


async def _show_admins(target: Message, session: AsyncSession) -> None:
    admins = await AdminService(session).list_all()
    text = messages.ADMINS_HEADER if admins else messages.ADMINS_EMPTY
    await target.answer(text, reply_markup=build_admins_list(admins))


async def _reject_protected(
    callback: CallbackQuery, session: AsyncSession, admin_id: int
) -> bool:
    """Alert + return True if the target is the caller or an env owner."""
    admin = await AdminService(session).get(admin_id)
    if admin is None:
        await callback.answer(messages.ADMIN_REMOVED)
        return True
    caller_id = callback.from_user.id if callback.from_user else None
    if admin.telegram_id == caller_id:
        await callback.answer(messages.ERR_CANNOT_SELF, show_alert=True)
        return True
    if AdminService.is_env_owner(admin.telegram_id):
        await callback.answer(messages.ERR_CANNOT_ENV_OWNER, show_alert=True)
        return True
    return False


@router.message(IsOwner(), F.text == messages.BTN_ADMINS)
async def admins_menu(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    await _show_admins(message, session)


@router.callback_query(IsOwner(), AdminCb.filter(F.action == "toggle"))
async def admin_toggle(
    callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession
) -> None:
    if await _reject_protected(callback, session, callback_data.id):
        return
    service = AdminService(session)
    admin = await service.get(callback_data.id)
    if admin is not None:
        await service.set_active(admin.id, not admin.is_active)
        log.info("admin_toggled", telegram_id=admin.telegram_id, active=not admin.is_active)
    if isinstance(callback.message, Message):
        admins = await service.list_all()
        try:
            await callback.message.edit_reply_markup(
                reply_markup=build_admins_list(admins)
            )
        except Exception:
            pass
    await callback.answer()


@router.callback_query(IsOwner(), AdminCb.filter(F.action == "remove"))
async def admin_remove(
    callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession
) -> None:
    if await _reject_protected(callback, session, callback_data.id):
        return
    service = AdminService(session)
    admin = await service.get(callback_data.id)
    if admin is not None:
        log.info("admin_removed", telegram_id=admin.telegram_id)
        await service.remove(admin.id)
    if isinstance(callback.message, Message):
        admins = await service.list_all()
        try:
            await callback.message.edit_reply_markup(
                reply_markup=build_admins_list(admins)
            )
        except Exception:
            pass
    await callback.answer(messages.ADMIN_REMOVED)


@router.callback_query(IsOwner(), AdminCb.filter(F.action == "add"))
async def admin_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminAdd.waiting)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.ASK_ADMIN)
    await callback.answer()


@router.message(IsOwner(), AdminAdd.waiting)
async def admin_add_input(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    text = (message.text or "").strip()
    forwarded = message.forward_from  # user who wrote the forwarded message

    telegram_id: int | None = None
    if text.isdigit():
        telegram_id = int(text)
    elif forwarded is not None:
        telegram_id = forwarded.id

    if telegram_id is None:
        await message.answer(messages.ADMIN_INVALID)
        return

    await AdminService(session).add_admin(telegram_id, role="admin")
    await state.clear()
    log.info("admin_added", telegram_id=telegram_id)
    await message.answer(messages.ADMIN_ADDED)
    await _show_admins(message, session)
