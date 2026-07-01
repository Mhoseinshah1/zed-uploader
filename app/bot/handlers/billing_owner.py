"""Owner billing: payment approval (idempotent) + sell settings."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import PayCb, SellCb
from app.bot.filters import IsOwner
from app.bot.keyboards.inline import build_sell
from app.bot.states import SellEdit
from app.core.logging import get_logger
from app.models.user import User
from app.services.bot_setting_service import (
    KEY_CARD_HOLDER,
    KEY_CARD_NUMBER,
    BotSettingService,
)
from app.services.payment_service import PaymentService
from app.services.plan_service import PlanService

router = Router(name="billing_owner")
log = get_logger("handler.billing_owner")


async def _notify_user(bot, session: AsyncSession, user_id: int, text: str) -> None:
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is not None:
        try:
            await bot.send_message(user.telegram_id, text)
        except Exception:
            pass


# --- payment approval ------------------------------------------------------
@router.callback_query(IsOwner(), PayCb.filter(F.action == "approve"))
async def payment_approve(
    callback: CallbackQuery, callback_data: PayCb, session: AsyncSession
) -> None:
    admin_id = callback.from_user.id if callback.from_user else 0
    status, payment = await PaymentService(session).approve(callback_data.id, admin_id)
    if status == "already":
        await callback.answer(messages.PAY_ALREADY, show_alert=True)
        return
    if status == "not_found" or payment is None:
        await callback.answer()
        return
    await _notify_user(
        callback.bot, session, payment.user_id, messages.user_credited(payment.amount)
    )
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    await callback.answer(messages.PAY_APPROVED)


@router.callback_query(IsOwner(), PayCb.filter(F.action == "reject"))
async def payment_reject(
    callback: CallbackQuery, callback_data: PayCb, session: AsyncSession
) -> None:
    admin_id = callback.from_user.id if callback.from_user else 0
    payment = await PaymentService(session).reject(callback_data.id, admin_id)
    if payment is not None:
        await _notify_user(
            callback.bot, session, payment.user_id, messages.USER_PAYMENT_REJECTED
        )
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    await callback.answer(messages.PAY_REJECTED)


# --- sell settings ---------------------------------------------------------
async def _show_sell(target: Message, session: AsyncSession) -> None:
    setting = BotSettingService(session)
    card = await setting.get_raw(KEY_CARD_NUMBER)
    holder = await setting.get_raw(KEY_CARD_HOLDER)
    plans = await PlanService(session).list_active()
    await target.answer(
        messages.sell_view(card, holder), reply_markup=build_sell(card, holder, plans)
    )


@router.message(IsOwner(), F.text == messages.BTN_SELL)
async def sell_menu(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _show_sell(message, session)


@router.callback_query(IsOwner(), SellCb.filter(F.action == "card"))
async def sell_card(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SellEdit.waiting_card)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.ASK_CARD)
    await callback.answer()


@router.callback_query(IsOwner(), SellCb.filter(F.action == "holder"))
async def sell_holder(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SellEdit.waiting_holder)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.ASK_HOLDER)
    await callback.answer()


@router.callback_query(IsOwner(), SellCb.filter(F.action == "price"))
async def sell_price(
    callback: CallbackQuery, callback_data: SellCb, state: FSMContext, session: AsyncSession
) -> None:
    plan = await PlanService(session).get(callback_data.key)
    if plan is None:
        await callback.answer(messages.PLAN_NOT_AVAILABLE, show_alert=True)
        return
    await state.set_state(SellEdit.waiting_price)
    await state.update_data(plan_key=plan.key)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.ask_price(plan.title))
    await callback.answer()


@router.callback_query(IsOwner(), SellCb.filter(F.action == "duration"))
async def sell_duration(
    callback: CallbackQuery, callback_data: SellCb, state: FSMContext, session: AsyncSession
) -> None:
    plan = await PlanService(session).get(callback_data.key)
    if plan is None:
        await callback.answer(messages.PLAN_NOT_AVAILABLE, show_alert=True)
        return
    await state.set_state(SellEdit.waiting_duration)
    await state.update_data(plan_key=plan.key)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.ask_duration(plan.title))
    await callback.answer()


@router.message(IsOwner(), SellEdit.waiting_card, F.text)
async def sell_card_input(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    await BotSettingService(session).set(KEY_CARD_NUMBER, (message.text or "").strip())
    log.info("sell_updated", field="card_number")
    await message.answer(messages.SELL_SAVED)
    await _show_sell(message, session)


@router.message(IsOwner(), SellEdit.waiting_holder, F.text)
async def sell_holder_input(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    await BotSettingService(session).set(KEY_CARD_HOLDER, (message.text or "").strip())
    log.info("sell_updated", field="card_holder")
    await message.answer(messages.SELL_SAVED)
    await _show_sell(message, session)


@router.message(IsOwner(), SellEdit.waiting_price, F.text)
async def sell_price_input(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer(messages.INVALID_AMOUNT)
        return
    data = await state.get_data()
    await state.clear()
    await PlanService(session).set_price(data["plan_key"], int(raw))
    log.info("sell_updated", field="price", plan=data["plan_key"])
    await message.answer(messages.SELL_SAVED)
    await _show_sell(message, session)


@router.message(IsOwner(), SellEdit.waiting_duration, F.text)
async def sell_duration_input(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer(messages.INVALID_AMOUNT)
        return
    data = await state.get_data()
    await state.clear()
    await PlanService(session).set_duration(data["plan_key"], int(raw))
    log.info("sell_updated", field="duration", plan=data["plan_key"])
    await message.answer(messages.SELL_SAVED)
    await _show_sell(message, session)
