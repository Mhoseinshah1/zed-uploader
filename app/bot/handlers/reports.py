"""Media abuse reporting (any user): 🚩 under a delivered file -> pick a reason.

Review happens in the web panel; the bot only files reports (deduped per
user+media by the service/unique constraint).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import ReportCb
from app.bot.keyboards.inline import build_report_reasons
from app.core.logging import get_logger
from app.models.user import User
from app.services.report_service import CREATED, ReportService

router = Router(name="reports")
log = get_logger("handler.reports")


@router.callback_query(ReportCb.filter(F.action == "start"))
async def report_start(callback: CallbackQuery, callback_data: ReportCb) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            messages.ASK_REPORT_REASON,
            reply_markup=build_report_reasons(callback_data.id),
        )
    await callback.answer()


@router.callback_query(ReportCb.filter(F.action == "reason"))
async def report_reason(
    callback: CallbackQuery,
    callback_data: ReportCb,
    session: AsyncSession,
    db_user: User | None,
) -> None:
    if db_user is None:
        await callback.answer()
        return
    result = await ReportService(session).create(
        callback_data.id, db_user.id, callback_data.value
    )
    text = messages.REPORT_THANKS if result == CREATED else messages.REPORT_DUPLICATE
    await callback.answer(text, show_alert=True)
