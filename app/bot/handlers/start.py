"""/start handler + force-join recheck + per-file password gate.

File delivery is delegated to ``deliver_by_code`` (app/bot/delivery.py), which
enforces status -> force-join gate -> password gate -> atomic claim -> send, and
is shared with the join-recheck callback below. When a file is password
protected the viewer is put into an FSM state and prompted; a wrong password is
counted and, after a few tries, that (user, code) is locked out via Redis.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import JoinCb
from app.bot.delivery import DeliveryStatus, deliver_by_code
from app.bot.keyboards.inline import build_join_gate
from app.bot.keyboards.reply import build_admin_menu, build_user_menu
from app.bot.states import Delivery
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.core.security import (
    clear_media_password_failures,
    media_password_locked,
    record_media_password_failure,
)
from app.models.user import User
from app.services.admin_service import AdminService
from app.services.media_service import MediaService
from app.services.text_service import get_text

router = Router(name="start")
log = get_logger("handler.start")

_STATUS_TEXT_KEYS = {
    DeliveryStatus.NOT_FOUND: "not_found",
    DeliveryStatus.INACTIVE: "inactive",
    DeliveryStatus.LIMIT_REACHED: "limit_reached",
    DeliveryStatus.FAILED: "generic_error",
    DeliveryStatus.BLOCKED: "account_blocked",
}


async def _status_text(session: AsyncSession, status: DeliveryStatus) -> str:
    return await get_text(session, _STATUS_TEXT_KEYS.get(status, "not_found"))


async def _send_welcome(
    message: Message, session: AsyncSession, db_user: User | None = None
) -> None:
    """Welcome; admins also get the persistent reply keyboard (owners: +extra)."""
    user = message.from_user
    # best-effort: log a first-time user to this tenant's log group (G1)
    if db_user is not None and getattr(db_user, "just_created", False):
        try:
            from app.services.tenant_logger import TenantLogger

            await TenantLogger(session).log_new_user(
                user.id if user else 0,
                (user.first_name or user.username) if user else None,
                bot=message.bot,
            )
        except Exception:
            pass
    welcome = await get_text(session, "welcome")
    from app.core.tenant_context import PLATFORM_TENANT_ID, current_tenant

    is_platform = current_tenant() == PLATFORM_TENANT_ID
    if user is not None and await AdminService.is_admin(session, user.id):
        is_owner = await AdminService.is_owner(session, user.id)
        await message.answer(
            welcome, reply_markup=build_admin_menu(is_owner, is_platform)
        )
        # keep this admin's chat-scoped command menu fresh (best-effort)
        from app.bot.commands_menu import push_admin_commands

        await push_admin_commands(message.bot, session, user.id)
    else:
        await message.answer(welcome, reply_markup=build_user_menu(is_platform))
    # best-effort start_message ad (never blocks the welcome)
    from app.bot.delivery import send_placement_ads

    await send_placement_ads(
        message.bot, session, message.chat.id,
        user.id if user else message.chat.id, "start_message",
    )


async def _reply_delivery(
    message: Message, state: FSMContext, result, code: str, session: AsyncSession
) -> None:
    """Translate a message-triggered delivery outcome into a user reply."""
    if result.status is DeliveryStatus.GATED:
        await message.answer(
            await get_text(session, "force_join"),
            reply_markup=build_join_gate(result.channels, code),
        )
    elif result.status is DeliveryStatus.PASSWORD_REQUIRED:
        await state.set_state(Delivery.waiting_password)
        await state.update_data(code=code)
        await message.answer(await get_text(session, "password_prompt"))
    elif result.status is not DeliveryStatus.DELIVERED:
        await message.answer(await _status_text(session, result.status))


@router.message(CommandStart(deep_link=True))
async def start_with_code(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    code = (command.args or "").strip()
    if not code:
        await _send_welcome(message, session, db_user)
        return

    result = await deliver_by_code(
        message.bot, session, message.chat.id, message.from_user, code
    )
    await _reply_delivery(message, state, result, code, session)


@router.callback_query(JoinCb.filter())
async def cb_join_recheck(
    callback: CallbackQuery,
    callback_data: JoinCb,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Re-run delivery after the user claims to have joined the channels."""
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    result = await deliver_by_code(
        callback.bot,
        session,
        callback.message.chat.id,
        callback.from_user,
        callback_data.code,
    )
    if result.status is DeliveryStatus.GATED:
        await callback.answer(messages.GATE_STILL, show_alert=True)
    elif result.status is DeliveryStatus.PASSWORD_REQUIRED:
        await state.set_state(Delivery.waiting_password)
        await state.update_data(code=callback_data.code)
        await callback.answer()
        await callback.message.answer(await get_text(session, "password_prompt"))
    elif result.status is DeliveryStatus.DELIVERED:
        await callback.answer()
        try:
            await callback.message.delete()
        except Exception:  # gate message already gone
            pass
    else:
        await callback.answer()
        try:
            await callback.message.edit_text(
                await _status_text(session, result.status)
            )
        except Exception:
            pass


# ~startswith("/"): a tapped menu command must reach its own handler instead
# of being counted as a wrong password (3 taps would lock the file 5 minutes).
@router.message(StateFilter(Delivery.waiting_password), F.text, ~F.text.startswith("/"))
async def input_delivery_password(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    """Verify a viewer-typed password, then deliver (or count the failure)."""
    data = await state.get_data()
    code = data.get("code")
    if not code:
        await state.clear()
        return

    user_id = message.from_user.id if message.from_user else message.chat.id
    redis = get_redis()
    if await media_password_locked(redis, user_id, code):
        await message.answer(messages.PASSWORD_LOCKED)
        return

    service = MediaService(session)
    media = await service.get_by_code(code)
    if media is None or not media.password_hash:
        # file vanished or lost its password meanwhile -> normal delivery attempt
        await state.clear()
        result = await deliver_by_code(
            message.bot, session, message.chat.id, message.from_user, code
        )
        await _reply_delivery(message, state, result, code, session)
        return

    typed = (message.text or "").strip()
    if MediaService.verify_password(media, typed):
        await clear_media_password_failures(redis, user_id, code)
        await state.clear()
        result = await deliver_by_code(
            message.bot,
            session,
            message.chat.id,
            message.from_user,
            code,
            password_verified=True,
        )
        await _reply_delivery(message, state, result, code, session)
        return

    remaining = await record_media_password_failure(redis, user_id, code)
    if remaining <= 0:
        await state.clear()
        await message.answer(messages.PASSWORD_LOCKED)
    else:
        await message.answer(messages.password_wrong(remaining))


@router.message(CommandStart())
async def start_plain(
    message: Message, session: AsyncSession, db_user: User | None
) -> None:
    await _send_welcome(message, session, db_user)
