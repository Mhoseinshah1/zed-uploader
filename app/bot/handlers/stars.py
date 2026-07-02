"""Telegram Stars (XTR) plan purchase: invoice -> pre_checkout -> activate.

The money logic lives in StarsService (idempotent on the Telegram charge id);
these handlers only translate Telegram updates.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import StarsBuyCb
from app.core.logging import get_logger
from app.models.user import User
from app.services.plan_service import PlanService
from app.services.stars_service import ACTIVATED, ALREADY, FAILED, StarsService

router = Router(name="stars")
log = get_logger("handler.stars")


@router.callback_query(StarsBuyCb.filter())
async def stars_buy(
    callback: CallbackQuery, callback_data: StarsBuyCb, session: AsyncSession
) -> None:
    from app.services.license_service import paid_features_allowed

    if not await paid_features_allowed(session):
        await callback.answer(messages.LICENSE_BLOCKED, show_alert=True)
        return
    plan = await PlanService(session).get(callback_data.plan)
    if plan is None or not plan.is_active or plan.stars_price is None:
        await callback.answer(messages.PLAN_NOT_AVAILABLE, show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer_invoice(
            title=messages.stars_invoice_title(plan.title),
            description=messages.stars_invoice_description(
                plan.title, plan.duration_days
            ),
            payload=f"plan:{plan.key}",
            currency="XTR",
            prices=[LabeledPrice(label=plan.title, amount=plan.stars_price)],
            provider_token="",  # Stars invoices use no provider token
        )
    await callback.answer()


@router.pre_checkout_query()
async def stars_pre_checkout(
    query: PreCheckoutQuery, session: AsyncSession
) -> None:
    error = await StarsService(session).validate_pre_checkout(
        query.invoice_payload, query.total_amount, query.currency
    )
    if error is None:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message=error)


@router.message(F.successful_payment)
async def stars_successful_payment(
    message: Message, session: AsyncSession, db_user: User | None
) -> None:
    sp = message.successful_payment
    if sp is None or db_user is None:
        return
    result = await StarsService(session).apply_successful_payment(
        db_user,
        sp.invoice_payload,
        sp.telegram_payment_charge_id,
        sp.total_amount,
        sp.currency,
    )
    if result == ACTIVATED:
        expires = db_user.plan_expires_at
        await message.answer(
            messages.plan_activated(expires.strftime("%Y-%m-%d") if expires else None)
        )
    elif result == ALREADY:
        await message.answer(messages.STARS_ALREADY)
    elif result == FAILED:
        await message.answer(messages.STARS_FAILED)
    else:  # invalid — log-only path already handled in the service
        await message.answer(messages.STARS_INVALID)
