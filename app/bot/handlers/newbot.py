"""Buy-a-bot factory flow (Phase F3) — PLATFORM (master) bot only.

Every handler is gated to the platform tenant (``current_tenant() == 1``): only
the master bot sells bots. Flow: show active bot plans + wallet balance -> the
customer picks one -> submits their BotFather token -> validate via getMe ->
atomic wallet charge + tenant create (BotCreationService) -> the bot is live.
The token message is deleted and never echoed back.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import NewBotCb
from app.bot.states import NewBot
from app.core.config import settings
from app.core.logging import get_logger
from app.core.tenant_context import PLATFORM_TENANT_ID, current_tenant
from app.db.session import async_session_maker
from app.models.user import User
from app.services.bot_creation_service import (
    BotCreationService,
    BotCreationStatus,
    validate_bot_token,
)
from app.services.bot_plan_service import BotPlanService
from app.services.wallet_service import WalletService

router = Router(name="newbot")
log = get_logger("handler.newbot")


def _is_platform() -> bool:
    return current_tenant() == PLATFORM_TENANT_ID


async def _show_plans(message: Message, session: AsyncSession, db_user: User | None) -> None:
    plans = await BotPlanService(session).list_active()
    if not plans:
        await message.answer(messages.NEWBOT_NO_PLANS)
        return
    balance = await WalletService(session).balance(db_user.id) if db_user else 0
    rows = [
        [
            InlineKeyboardButton(
                text=messages.newbot_plan_label(p.title, p.price, p.duration_days),
                callback_data=NewBotCb(plan_key=p.key).pack(),
            )
        ]
        for p in plans
    ]
    await message.answer(
        messages.newbot_plans_view(balance),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.message(Command("newbot"))
async def newbot_command(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    await state.clear()
    if not _is_platform():
        await message.answer(messages.NEWBOT_ONLY_PLATFORM)
        return
    await _show_plans(message, session, db_user)


@router.message(F.text == messages.BTN_CREATE_BOT)
async def newbot_button(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    await state.clear()
    if not _is_platform():
        return
    await _show_plans(message, session, db_user)


@router.callback_query(NewBotCb.filter())
async def newbot_pick_plan(
    callback: CallbackQuery, callback_data: NewBotCb, state: FSMContext
) -> None:
    if not _is_platform():
        await callback.answer()
        return
    await state.set_state(NewBot.waiting_token)
    await state.update_data(plan_key=callback_data.plan_key)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.NEWBOT_ASK_TOKEN)
    await callback.answer()


@router.message(NewBot.waiting_token, F.text)
async def newbot_receive_token(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None,
    registry=None,
) -> None:
    if not _is_platform():
        await state.clear()
        return
    data = await state.get_data()
    plan_key = data.get("plan_key")
    token = (message.text or "").strip()
    await state.clear()
    # never keep or echo the token
    try:
        await message.delete()
    except Exception:
        pass
    if db_user is None or not plan_key:
        return

    try:
        bot_id, username = await validate_bot_token(token)
    except Exception:
        await message.answer(messages.NEWBOT_INVALID_TOKEN)
        return

    service = BotCreationService(session, async_session_maker, registry)
    result = await service.create_from_wallet(
        owner_user_id=db_user.id,
        owner_telegram_id=message.from_user.id if message.from_user else 0,
        plan_key=plan_key,
        bot_id=bot_id,
        bot_username=username,
        bot_token=token,
    )
    await message.answer(_result_text(result))


def _result_text(result) -> str:
    if result.status == BotCreationStatus.OK:
        panel_url = f"{settings.domain.rstrip('/')}{settings.panel_path}"
        return messages.newbot_success(result.bot_username, panel_url, result.expires_at)
    return {
        BotCreationStatus.ALREADY_REGISTERED: messages.NEWBOT_ALREADY,
        BotCreationStatus.DUPLICATE: messages.NEWBOT_ALREADY,
        BotCreationStatus.INSUFFICIENT: messages.NEWBOT_INSUFFICIENT,
        BotCreationStatus.NOT_AVAILABLE: messages.NEWBOT_NO_PLANS,
    }.get(result.status, messages.NEWBOT_FAILED)
