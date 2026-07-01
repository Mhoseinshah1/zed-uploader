"""/start handler + force-join recheck.

File delivery is delegated to ``deliver_by_code`` (app/bot/delivery.py), which
enforces status -> force-join gate -> atomic claim -> send, and is shared with
the join-recheck callback below.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import JoinCb
from app.bot.delivery import DeliveryStatus, deliver_by_code
from app.bot.keyboards.inline import build_join_gate
from app.bot.keyboards.reply import build_admin_menu, build_user_menu
from app.core.logging import get_logger
from app.models.user import User
from app.services.admin_service import AdminService

router = Router(name="start")
log = get_logger("handler.start")

_STATUS_MESSAGES = {
    DeliveryStatus.NOT_FOUND: messages.NOT_FOUND,
    DeliveryStatus.INACTIVE: messages.INACTIVE,
    DeliveryStatus.LIMIT_REACHED: messages.LIMIT_REACHED,
    DeliveryStatus.FAILED: messages.GENERIC_ERROR,
}


async def _send_welcome(message: Message, session: AsyncSession) -> None:
    """Welcome; admins also get the persistent reply keyboard (owners: +extra)."""
    user = message.from_user
    if user is not None and await AdminService.is_admin(session, user.id):
        is_owner = await AdminService.is_owner(session, user.id)
        await message.answer(messages.WELCOME, reply_markup=build_admin_menu(is_owner))
    else:
        await message.answer(messages.WELCOME, reply_markup=build_user_menu())


@router.message(CommandStart(deep_link=True))
async def start_with_code(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    code = (command.args or "").strip()
    if not code:
        await _send_welcome(message, session)
        return

    result = await deliver_by_code(
        message.bot, session, message.chat.id, message.from_user, code
    )
    if result.status is DeliveryStatus.GATED:
        await message.answer(
            messages.GATE_PROMPT, reply_markup=build_join_gate(result.channels, code)
        )
    elif result.status is not DeliveryStatus.DELIVERED:
        await message.answer(_STATUS_MESSAGES.get(result.status, messages.NOT_FOUND))


@router.callback_query(JoinCb.filter())
async def cb_join_recheck(
    callback: CallbackQuery, callback_data: JoinCb, session: AsyncSession
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
                _STATUS_MESSAGES.get(result.status, messages.NOT_FOUND)
            )
        except Exception:
            pass


@router.message(CommandStart())
async def start_plain(
    message: Message, session: AsyncSession, db_user: User | None
) -> None:
    await _send_welcome(message, session)
