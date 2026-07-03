"""Media comments (J8): view approved comments + write one (moderated)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import CommentCb
from app.core.logging import get_logger
from app.models.user import User
from app.services.comment_service import CommentService

router = Router(name="comments")
log = get_logger("handler.comments")


class CommentWrite(StatesGroup):
    waiting_body = State()  # data: media_id


@router.callback_query(CommentCb.filter(F.action == "open"))
async def comments_open(
    callback: CallbackQuery, callback_data: CommentCb, session: AsyncSession
) -> None:
    rows = await CommentService(session).approved_for(callback_data.id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            messages.comments_view(rows),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text=messages.LBL_WRITE_COMMENT,
                    callback_data=CommentCb(action="write", id=callback_data.id).pack(),
                )
            ]]),
        )
    await callback.answer()


@router.callback_query(CommentCb.filter(F.action == "write"))
async def comment_write(
    callback: CallbackQuery, callback_data: CommentCb, state: FSMContext
) -> None:
    await state.set_state(CommentWrite.waiting_body)
    await state.update_data(media_id=callback_data.id)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.ASK_COMMENT)
    await callback.answer()


@router.message(StateFilter(CommentWrite.waiting_body), F.text)
async def comment_body(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    body = (message.text or "").strip()
    data = await state.get_data()
    await state.clear()
    if not body or db_user is None:
        return
    await CommentService(session).create(int(data["media_id"]), db_user.id, body)
    log.info("comment_created", media_id=data["media_id"], user_id=db_user.id)
    await message.answer(messages.COMMENT_SAVED)
