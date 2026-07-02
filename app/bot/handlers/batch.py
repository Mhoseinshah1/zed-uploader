"""Batch (multi-file) upload — button-driven, Redis-buffered.

Reuses ``extract_file`` from the single-file upload handler and the existing
``MediaService.create_media`` (which already accepts a list of files).
"""
from __future__ import annotations

import json

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import BatchCb
from app.bot.filters import IsAdmin
from app.bot.gating import feature_allowed, within_file_limit
from app.bot.handlers.upload import MEDIA_FILTER, extract_file
from app.bot.keyboards.inline import build_batch_controls, build_open_plans
from app.bot.states import Upload
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.models.user import User
from app.services.bot_setting_service import BotSettingService
from app.services.feature_service import FeatureService
from app.services.media_service import MediaService
from app.services.plan_service import PlanService

router = Router(name="batch")
log = get_logger("handler.batch")

BATCH_TTL = 3600


def _key(telegram_id: int) -> str:
    return f"batch:{telegram_id}"


@router.message(IsAdmin(), F.text == messages.BTN_BATCH_UPLOAD)
async def batch_start(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User | None
) -> None:
    await state.clear()
    if message.from_user is None:
        return
    if not await feature_allowed(session, "batch_upload", db_user, message.from_user.id):
        required = await FeatureService.required_plan(session, "batch_upload")
        await message.answer(
            messages.requires_plan(required), reply_markup=build_open_plans()
        )
        return
    await get_redis().delete(_key(message.from_user.id))
    await state.set_state(Upload.collecting)
    await message.answer(messages.BATCH_START, reply_markup=build_batch_controls())


@router.message(IsAdmin(), Upload.collecting, MEDIA_FILTER)
async def batch_collect(message: Message) -> None:
    if message.from_user is None:
        return
    extracted = extract_file(message)
    if extracted is None:
        await message.answer(messages.UNSUPPORTED_UPLOAD)
        return
    file_data, caption = extracted
    redis = get_redis()
    key = _key(message.from_user.id)
    count = await redis.rpush(key, json.dumps({"file": file_data, "caption": caption}))
    await redis.expire(key, BATCH_TTL)
    await message.answer(messages.batch_added(count))


@router.message(IsAdmin(), Upload.collecting, F.text)
async def batch_hint(message: Message) -> None:
    await message.answer(messages.BATCH_HINT)


@router.callback_query(IsAdmin(), Upload.collecting, BatchCb.filter(F.action == "finish"))
async def batch_finish(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User | None,
) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    redis = get_redis()
    key = _key(callback.from_user.id)
    raw_items = await redis.lrange(key, 0, -1)
    if not raw_items:
        await callback.answer(messages.BATCH_EMPTY, show_alert=True)
        return

    # enforce the plan's max_files (one batch = one media item)
    tg_id = callback.from_user.id
    if not await within_file_limit(session, db_user, tg_id):
        limit = await PlanService(session).max_files(
            db_user.effective_plan if db_user else "free"
        )
        if isinstance(callback.message, Message):
            await callback.message.answer(
                messages.file_limit_reached(limit or 0), reply_markup=build_open_plans()
            )
        await callback.answer()
        return

    items = [json.loads(r) for r in raw_items]
    files = [it["file"] for it in items]
    caption = items[0].get("caption")

    setting_service = BotSettingService(session)
    protect = await setting_service.effective_protect()
    autodelete = await setting_service.effective_autodelete()

    service = MediaService(session)
    media = await service.create_media(
        files=files,
        owner_user_id=db_user.id if db_user else None,
        caption=caption,
        protect_content=protect,
        auto_delete_seconds=autodelete or None,
    )
    await redis.delete(key)
    await state.clear()
    log.info("batch_created", media_id=media.id, code=media.code, count=len(files))
    if isinstance(callback.message, Message):
        await callback.message.answer(
            messages.batch_done(await service.deep_link(media), media.code, len(files))
        )
    await callback.answer()


@router.callback_query(IsAdmin(), BatchCb.filter(F.action == "cancel"))
async def batch_cancel(
    callback: CallbackQuery, state: FSMContext
) -> None:
    if callback.from_user is not None:
        await get_redis().delete(_key(callback.from_user.id))
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.BATCH_CANCELLED)
    await callback.answer()
