"""Upload review queue (admins).

Admins list `pending` user uploads, preview a file's contents, and approve or
reject each. Approve sends the deep link to the uploader; reject asks for a
reason and forwards it. All handlers are IsAdmin-gated.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import ReviewCb
from app.bot.filters import IsAdmin
from app.bot.keyboards.inline import build_review_list
from app.bot.sender import send_media_file
from app.bot.states import Review
from app.core.logging import get_logger
from app.services.media_service import MediaService

router = Router(name="review")
log = get_logger("handler.review")

PAGE_SIZE = 5


async def _render_queue(
    session: AsyncSession, page: int
) -> tuple[str, object | None]:
    service = MediaService(session)
    total = await service.count_pending()
    if total == 0:
        return messages.REVIEW_QUEUE_EMPTY, None
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    items = await service.list_pending(limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    return (
        messages.review_queue_header(total, page + 1, total_pages),
        build_review_list(items, page, total_pages),
    )


async def _notify_uploader(bot, service: MediaService, media, text: str) -> None:
    owner_tg = await service.owner_telegram_id(media.owner_user_id)
    if owner_tg:
        try:
            await bot.send_message(owner_tg, text)
        except Exception as exc:  # uploader blocked the bot / never started it
            log.warning("review_notify_failed", media_id=media.id, error=str(exc))


@router.message(IsAdmin(), F.text == messages.BTN_REVIEW)
async def review_menu(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    text, markup = await _render_queue(session, 0)
    await message.answer(text, reply_markup=markup)


@router.callback_query(IsAdmin(), ReviewCb.filter(F.action == "list"))
async def review_page(
    callback: CallbackQuery, callback_data: ReviewCb, session: AsyncSession
) -> None:
    if isinstance(callback.message, Message):
        text, markup = await _render_queue(session, callback_data.page)
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception:
            pass
    await callback.answer()


@router.callback_query(IsAdmin(), ReviewCb.filter(F.action == "view"))
async def review_view(
    callback: CallbackQuery, callback_data: ReviewCb, session: AsyncSession
) -> None:
    service = MediaService(session)
    media = await service.get_pending(callback_data.id)
    if media is None or not isinstance(callback.message, Message):
        await callback.answer(messages.REVIEW_GONE, show_alert=True)
        return
    for index, media_file in enumerate(media.files):
        try:
            await send_media_file(
                callback.bot,
                callback.message.chat.id,
                media_file,
                caption=media.caption if index == 0 else None,
            )
        except Exception as exc:
            log.warning("review_preview_failed", media_id=media.id, error=str(exc))
    await callback.answer()


@router.callback_query(IsAdmin(), ReviewCb.filter(F.action == "approve"))
async def review_approve(
    callback: CallbackQuery, callback_data: ReviewCb, session: AsyncSession
) -> None:
    service = MediaService(session)
    tg_id = callback.from_user.id if callback.from_user else None
    media = await service.approve(callback_data.id, tg_id)
    if media is None:
        await callback.answer(messages.REVIEW_GONE, show_alert=True)
        return
    log.info("upload_approved", media_id=media.id, by=tg_id)
    from app.services.preview_service import maybe_post_preview

    await maybe_post_preview(session, media, bot=callback.bot)  # J5
    await _notify_uploader(
        callback.bot,
        service,
        media,
        messages.upload_approved_notify(await service.deep_link(media), media.code),
    )
    if isinstance(callback.message, Message):
        text, markup = await _render_queue(session, callback_data.page)
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception:
            pass
    await callback.answer(messages.REVIEW_APPROVED)


@router.callback_query(IsAdmin(), ReviewCb.filter(F.action == "reject"))
async def review_reject_prompt(
    callback: CallbackQuery,
    callback_data: ReviewCb,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    media = await MediaService(session).get_pending(callback_data.id)
    if media is None:
        await callback.answer(messages.REVIEW_GONE, show_alert=True)
        return
    await state.set_state(Review.waiting_reason)
    await state.update_data(media_id=callback_data.id, page=callback_data.page)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.ASK_REJECT_REASON)
    await callback.answer()


@router.message(IsAdmin(), Review.waiting_reason, F.text)
async def review_reject_reason(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    reason = None if raw == "-" else raw
    data = await state.get_data()
    await state.clear()
    media_id, page = int(data["media_id"]), int(data.get("page", 0))

    service = MediaService(session)
    tg_id = message.from_user.id if message.from_user else None
    media = await service.reject(media_id, tg_id, note=reason)
    if media is None:
        await message.answer(messages.REVIEW_GONE)
        return
    log.info("upload_rejected", media_id=media.id, by=tg_id)
    await _notify_uploader(
        message.bot, service, media, messages.upload_rejected_notify(reason)
    )
    await message.answer(messages.REVIEW_REJECTED)
    text, markup = await _render_queue(session, page)
    await message.answer(text, reply_markup=markup)
