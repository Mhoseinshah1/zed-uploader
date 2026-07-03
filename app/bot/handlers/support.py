"""In-bot support / ticketing (H2).

"🎧 پشتیبانی" opens or continues a ticket. The target is decided by the
opener's role in the CURRENT tenant:
  * a regular end-user  -> target=tenant_admin  (goes to this bot's admins);
  * a tenant admin/owner -> target=platform      (a reseller contacting the
    platform operator; surfaced only in the super-admin panel inbox).

Everything runs inside the per-update tenant context set by the tenant
middleware, so tickets/messages are stamped + filtered to this tenant. New
tenant_admin messages best-effort DM the tenant's admins via THIS bot; platform
tickets are surfaced in the super-admin inbox (no cross-bot DM).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import SupportCb
from app.bot.states import Support
from app.core.logging import get_logger
from app.models.user import User
from app.services.admin_service import AdminService
from app.services.support_service import SupportService, notify_tenant_admins

router = Router(name="support")
log = get_logger("handler.support")


def _ticket_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=messages.SUPPORT_BTN_NEW_MSG,
                    callback_data=SupportCb(action="reply", id=ticket_id).pack(),
                ),
                InlineKeyboardButton(
                    text=messages.SUPPORT_BTN_CLOSE,
                    callback_data=SupportCb(action="close", id=ticket_id).pack(),
                ),
            ]
        ]
    )


async def _show_ticket(target: Message, session: AsyncSession, ticket) -> None:
    svc = SupportService(session)
    msgs = await svc.messages(ticket.id)
    lines = [
        f"{'👤' if m.sender_kind == 'user' else '🛟'} {m.body}" for m in msgs[-6:]
    ]
    await target.answer(
        messages.support_ticket_view(ticket.subject, ticket.status, lines),
        reply_markup=_ticket_keyboard(ticket.id),
    )


async def _notify_new_user_message(message: Message, session: AsyncSession, ticket) -> None:
    """A user wrote on a tenant_admin ticket -> ping this tenant's admins."""
    if ticket.target == "tenant_admin":
        await notify_tenant_admins(
            message.bot, session, messages.support_admin_notify(ticket.subject)
        )


@router.message(F.text == messages.BTN_SUPPORT)
async def support_menu(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    await state.clear()
    if db_user is None or message.from_user is None:
        return
    is_admin = await AdminService.is_admin(session, message.from_user.id)
    target = "platform" if is_admin else "tenant_admin"
    existing = await SupportService(session).active_ticket_for(db_user.id, target)
    if existing is not None:
        await _show_ticket(message, session, existing)
        return
    await state.set_state(Support.waiting_subject)
    await state.update_data(target=target)
    intro = messages.SUPPORT_INTRO_PLATFORM if target == "platform" else messages.SUPPORT_INTRO_USER
    await message.answer(intro)
    await message.answer(messages.SUPPORT_ASK_SUBJECT)


@router.message(Support.waiting_subject, F.text)
async def support_subject(message: Message, state: FSMContext) -> None:
    subject = (message.text or "").strip()
    if not subject:
        await message.answer(messages.SUPPORT_ASK_SUBJECT)
        return
    await state.update_data(subject=subject)
    await state.set_state(Support.waiting_message)
    await message.answer(messages.SUPPORT_ASK_MESSAGE)


@router.message(Support.waiting_message, F.text)
async def support_message(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    body = (message.text or "").strip()
    data = await state.get_data()
    if not body:
        await message.answer(messages.SUPPORT_EMPTY_BODY)
        return
    if db_user is None:
        await state.clear()
        return
    svc = SupportService(session)
    ticket_id = data.get("ticket_id")
    if ticket_id:
        ticket, _msg = await svc.add_message(int(ticket_id), "user", body)
        await state.clear()
        if ticket is None:
            return
        await _notify_new_user_message(message, session, ticket)
        await message.answer(messages.SUPPORT_REPLY_SENT)
    else:
        target = data.get("target", "tenant_admin")
        subject = data.get("subject", "—")
        ticket = await svc.open_ticket(db_user.id, subject, body, target)
        await state.clear()
        await _notify_new_user_message(message, session, ticket)
        await message.answer(messages.SUPPORT_CREATED)


@router.callback_query(SupportCb.filter(F.action == "reply"))
async def support_reply(
    callback: CallbackQuery,
    callback_data: SupportCb,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    ticket = await SupportService(session).get(callback_data.id)
    if ticket is None or db_user is None or ticket.opener_user_id != db_user.id:
        await callback.answer()
        return
    await state.set_state(Support.waiting_message)
    await state.update_data(ticket_id=ticket.id, target=ticket.target)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.SUPPORT_ASK_MESSAGE)
    await callback.answer()


@router.callback_query(SupportCb.filter(F.action == "close"))
async def support_close(
    callback: CallbackQuery,
    callback_data: SupportCb,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    ticket = await SupportService(session).get(callback_data.id)
    if ticket is None or db_user is None or ticket.opener_user_id != db_user.id:
        await callback.answer()
        return
    await SupportService(session).close_ticket(ticket.id)
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await callback.answer(messages.SUPPORT_CLOSED, show_alert=True)
