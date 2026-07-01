"""User-facing billing: wallet, top-up (card), transactions, subscription, buy."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import BuyCb, BuyOnlineCb, PayCheckCb, SubCb, WalletCb
from app.bot.keyboards.inline import (
    build_buy_confirm,
    build_centralpay,
    build_open_plans,
    build_plans,
    build_topup_methods,
    build_wallet,
)
from app.bot.states import Topup
from app.core.config import settings
from app.core.logging import get_logger
from app.models.user import User
from app.services.admin_service import AdminService
from app.services.bot_setting_service import (
    DEFAULT_TOPUP_MIN,
    KEY_CARD_HOLDER,
    KEY_CARD_NUMBER,
    KEY_TOPUP_MIN,
    BotSettingService,
)
from app.services.centralpay_service import CentralPayService
from app.services.payment_service import PaymentService
from app.services.plan_service import PlanService
from app.services.subscription_service import PurchaseStatus, SubscriptionService
from app.services.wallet_service import WalletService

router = Router(name="billing")
log = get_logger("handler.billing")


async def _send_centralpay_link(
    message: Message, session: AsyncSession, user: User, amount: int, intent: str
) -> None:
    started = await CentralPayService(session).start(user, amount, intent)
    if started is None:
        await message.answer(messages.CENTRALPAY_START_FAILED)
        return
    order_id, redirect_url = started
    await message.answer(
        messages.CENTRALPAY_PENDING, reply_markup=build_centralpay(redirect_url, order_id)
    )


def _fmt_date(dt) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


# --- wallet ----------------------------------------------------------------
@router.message(F.text == messages.BTN_WALLET)
async def wallet_menu(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    await state.clear()
    if db_user is None:
        return
    balance = await WalletService(session).balance(db_user.id)
    await message.answer(messages.wallet_view(balance), reply_markup=build_wallet())


@router.callback_query(WalletCb.filter(F.action == "tx"))
async def wallet_transactions(
    callback: CallbackQuery, session: AsyncSession, db_user: User | None
) -> None:
    if db_user is not None and isinstance(callback.message, Message):
        rows = await WalletService(session).last_transactions(db_user.id, limit=10)
        await callback.message.answer(messages.transactions_view(rows))
    await callback.answer()


@router.callback_query(WalletCb.filter(F.action == "topup"))
async def topup_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    if settings.centralpay_enabled:
        await callback.message.answer(
            messages.CHOOSE_TOPUP_METHOD,
            reply_markup=build_topup_methods(settings.centralpay_enabled),
        )
    else:
        await _ask_card_amount(callback.message, state, session)
    await callback.answer()


async def _ask_card_amount(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    card = await BotSettingService(session).get_raw(KEY_CARD_NUMBER)
    if not card:
        await message.answer(messages.PAYMENT_DISABLED)
        return
    minimum = await BotSettingService(session).get_int(KEY_TOPUP_MIN, DEFAULT_TOPUP_MIN)
    await state.update_data(method="card")
    await state.set_state(Topup.waiting_amount)
    await message.answer(
        f"{messages.ASK_TOPUP_AMOUNT}\n{messages.min_amount_hint(minimum)}"
    )


@router.callback_query(WalletCb.filter(F.action == "card"))
async def topup_card(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if isinstance(callback.message, Message):
        await _ask_card_amount(callback.message, state, session)
    await callback.answer()


@router.callback_query(WalletCb.filter(F.action == "online"))
async def topup_online(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not settings.centralpay_enabled:
        await callback.answer(messages.CENTRALPAY_DISABLED, show_alert=True)
        return
    minimum = await BotSettingService(session).get_int(KEY_TOPUP_MIN, DEFAULT_TOPUP_MIN)
    await state.update_data(method="online")
    await state.set_state(Topup.waiting_amount)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"{messages.ASK_ONLINE_AMOUNT}\n{messages.min_amount_hint(minimum)}"
        )
    await callback.answer()


@router.message(Topup.waiting_amount, F.text)
async def topup_amount(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    raw = (message.text or "").strip()
    setting = BotSettingService(session)
    minimum = await setting.get_int(KEY_TOPUP_MIN, DEFAULT_TOPUP_MIN)
    if not raw.isdigit() or int(raw) < minimum:
        await message.answer(messages.INVALID_AMOUNT)
        return
    amount = int(raw)
    data = await state.get_data()

    if data.get("method") == "online":
        await state.clear()
        if db_user is not None:
            await _send_centralpay_link(message, session, db_user, amount, intent="topup")
        return

    # card path
    card = await setting.get_raw(KEY_CARD_NUMBER)
    holder = await setting.get_raw(KEY_CARD_HOLDER) or "-"
    if not card:
        await state.clear()
        await message.answer(messages.PAYMENT_DISABLED)
        return
    await state.update_data(amount=amount)
    await state.set_state(Topup.waiting_receipt)
    await message.answer(messages.topup_instructions(card, holder, amount))


@router.message(Topup.waiting_receipt, F.photo | F.text)
async def topup_receipt(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    data = await state.get_data()
    amount = int(data.get("amount", 0))
    await state.clear()
    if db_user is None or amount <= 0:
        return

    receipt = message.photo[-1].file_id if message.photo else (message.text or "").strip()
    payment = await PaymentService(session).create(
        db_user.id, amount, method="card", receipt=receipt
    )
    log.info("payment_created", payment_id=payment.id, user_id=db_user.id, amount=amount)
    await message.answer(messages.TOPUP_PENDING)

    # notify all owners with approve/reject actions
    from app.bot.keyboards.inline import build_payment_actions

    owner_ids = await AdminService.owner_telegram_ids(session)
    text = messages.payment_notify(db_user.telegram_id, amount, "card", payment.id)
    for owner_id in owner_ids:
        try:
            await message.bot.send_message(
                owner_id, text, reply_markup=build_payment_actions(payment.id)
            )
        except Exception:  # owner hasn't started the bot / blocked it
            continue


# --- subscription ----------------------------------------------------------
async def _show_subscription(target: Message, session: AsyncSession, user: User) -> None:
    plans = await PlanService(session).list_active()
    expires = _fmt_date(user.plan_expires_at) if user.plan_expires_at else None
    await target.answer(
        messages.subscription_view(user.effective_plan, expires),
        reply_markup=build_plans(plans),
    )


@router.message(F.text == messages.BTN_SUBSCRIPTION)
async def subscription_menu(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    await state.clear()
    if db_user is not None:
        await _show_subscription(message, session, db_user)


@router.callback_query(SubCb.filter(F.action == "open"))
async def subscription_open(
    callback: CallbackQuery, session: AsyncSession, db_user: User | None
) -> None:
    if db_user is not None and isinstance(callback.message, Message):
        await _show_subscription(callback.message, session, db_user)
    await callback.answer()


@router.callback_query(BuyCb.filter(F.ok == 0))
async def buy_prompt(
    callback: CallbackQuery, callback_data: BuyCb, session: AsyncSession
) -> None:
    plan = await PlanService(session).get(callback_data.plan)
    if plan is None or not plan.is_active:
        await callback.answer(messages.PLAN_NOT_AVAILABLE, show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            messages.buy_confirm(plan.title, plan.price),
            reply_markup=build_buy_confirm(plan.key, settings.centralpay_enabled),
        )
    await callback.answer()


@router.callback_query(BuyCb.filter(F.ok == 1))
async def buy_confirm(
    callback: CallbackQuery, callback_data: BuyCb, session: AsyncSession, db_user: User | None
) -> None:
    if db_user is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    result = await SubscriptionService(session).purchase(db_user, callback_data.plan)
    if result.status is PurchaseStatus.OK:
        await callback.message.answer(
            messages.plan_activated(_fmt_date(result.expires_at) if result.expires_at else None)
        )
        await callback.answer()
    elif result.status is PurchaseStatus.INSUFFICIENT:
        balance = await WalletService(session).balance(db_user.id)
        await callback.message.answer(
            messages.insufficient_funds(balance, result.price),
            reply_markup=build_wallet(),
        )
        await callback.answer()
    else:
        await callback.answer(messages.PLAN_NOT_AVAILABLE, show_alert=True)


# --- CentralPay online (Phase 5) -------------------------------------------
@router.callback_query(BuyOnlineCb.filter())
async def buy_online(
    callback: CallbackQuery,
    callback_data: BuyOnlineCb,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    if not settings.centralpay_enabled:
        await callback.answer(messages.CENTRALPAY_DISABLED, show_alert=True)
        return
    plan = await PlanService(session).get(callback_data.plan)
    if plan is None or not plan.is_active:
        await callback.answer(messages.PLAN_NOT_AVAILABLE, show_alert=True)
        return
    if db_user is not None and isinstance(callback.message, Message):
        await _send_centralpay_link(
            callback.message, session, db_user, plan.price, intent=f"plan:{plan.key}"
        )
    await callback.answer()


@router.callback_query(PayCheckCb.filter())
async def payment_check(
    callback: CallbackQuery,
    callback_data: PayCheckCb,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    result = await CentralPayService(session).verify_and_apply(callback_data.order_id)
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    if result == "credited":
        balance = await WalletService(session).balance(db_user.id) if db_user else 0
        await callback.message.answer(messages.centralpay_credited(balance))
    elif result == "already":
        await callback.answer(messages.CENTRALPAY_ALREADY, show_alert=True)
        return
    elif result == "mismatch":
        await callback.message.answer(messages.CENTRALPAY_MISMATCH)
    else:  # failed
        await callback.message.answer(messages.CENTRALPAY_FAILED)
    await callback.answer()
