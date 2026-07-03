"""Custom reply-keyboard buttons (J8) — tenant-defined, whitelisted actions.

A DB-backed filter matches the incoming text against THIS tenant's active
buttons (built-in buttons are registered on earlier routers, so they always
win). Behaviors: url -> a link button; message -> the stored text; action ->
one of the code-defined ACTION_WHITELIST behaviors, never arbitrary code.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.keyboards.inline import build_url_button
from app.core.logging import get_logger
from app.models.custom_button import CustomButton
from app.models.user import User
from app.services.custom_button_service import ACTION_WHITELIST, CustomButtonService

router = Router(name="custom_buttons")
log = get_logger("handler.custom_buttons")


class MatchesCustomButton(BaseFilter):
    """Pass when the text equals one of this tenant's active button labels.

    Returns the matched row via filter-data injection (aiogram merges the dict
    into handler kwargs).
    """

    async def __call__(self, message: Message, session: AsyncSession):
        text = (message.text or "").strip()
        if not text:
            return False
        button = await CustomButtonService(session).by_label(text)
        if button is None:
            return False
        return {"custom_button": button}


@router.message(F.text, MatchesCustomButton())
async def handle_custom_button(
    message: Message,
    session: AsyncSession,
    db_user: User | None,
    custom_button: CustomButton,
) -> None:
    if custom_button.type == "url":
        await message.answer(
            custom_button.label,
            reply_markup=build_url_button(custom_button.label, custom_button.value),
        )
    elif custom_button.type == "message":
        await message.answer(custom_button.value)
    elif custom_button.type == "action":
        await _run_action(message, session, db_user, custom_button.value)
    log.info("custom_button", label=custom_button.label, type=custom_button.type)


async def _run_action(
    message: Message, session: AsyncSession, db_user: User | None, action: str
) -> None:
    """ONLY whitelisted, code-defined behaviors — an unknown value is a no-op."""
    if action not in ACTION_WHITELIST:
        return
    if action == "help":
        from app.services.text_service import get_text

        await message.answer(await get_text(session, "help"))
    elif action == "wallet":
        from app.bot.keyboards.inline import build_wallet
        from app.services.wallet_service import WalletService

        if db_user is not None:
            balance = await WalletService(session).balance(db_user.id)
            await message.answer(
                messages.wallet_view(balance), reply_markup=build_wallet()
            )
