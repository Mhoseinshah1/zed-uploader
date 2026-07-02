"""Broadcast composition (owners only). The worker does the actual sending."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import BcastCb
from app.bot.filters import IsOwner
from app.bot.keyboards.inline import build_broadcast_confirm
from app.bot.states import Broadcast
from app.core.logging import get_logger
from app.services import broadcast as broadcast_service

router = Router(name="broadcast")
log = get_logger("handler.broadcast")


@router.message(IsOwner(), F.text == messages.BTN_BROADCAST)
async def broadcast_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Broadcast.waiting_message)
    await message.answer(messages.BROADCAST_ASK)


@router.message(IsOwner(), Broadcast.waiting_message)
async def broadcast_capture(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await state.update_data(
        from_chat_id=message.chat.id, message_id=message.message_id
    )
    await state.set_state(Broadcast.confirming)
    count = await broadcast_service.audience_count(session)
    await message.answer(
        messages.broadcast_confirm(count), reply_markup=build_broadcast_confirm()
    )


@router.callback_query(IsOwner(), BcastCb.filter(F.action == "confirm"))
async def broadcast_confirm(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    from app.services.license_service import paid_features_allowed

    if not await paid_features_allowed(session):
        await callback.answer(messages.LICENSE_BLOCKED, show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    from_chat_id = data.get("from_chat_id")
    message_id = data.get("message_id")
    if from_chat_id is None or message_id is None:
        await callback.answer(messages.BROADCAST_NO_MESSAGE, show_alert=True)
        return
    requested_by = callback.from_user.id if callback.from_user else int(from_chat_id)
    job = await broadcast_service.create_job(
        session,
        from_chat_id=int(from_chat_id),
        message_id=int(message_id),
        created_by=requested_by,
    )
    log.info("broadcast_enqueued", job_id=job.id, total=job.total, requested_by=requested_by)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.BROADCAST_STARTED)
    await callback.answer()


@router.callback_query(IsOwner(), BcastCb.filter(F.action == "cancel"))
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.BROADCAST_CANCELLED)
    await callback.answer()
