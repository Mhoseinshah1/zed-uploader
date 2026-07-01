"""Telegram webhook route.

Protected by the secret-token header (Telegram sends it with every call), NOT
by the API rate limiter. Validates the header, then feeds the update to the
dispatcher stored on ``app.state`` during lifespan startup.
"""
from __future__ import annotations

from aiogram.types import Update
from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import settings
from app.core.logging import get_logger

router = APIRouter(tags=["telegram"])
log = get_logger("webhook")


@router.post(settings.webhook_path)
async def telegram_webhook(request: Request) -> dict[str, bool]:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != settings.webhook_secret:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid secret token")

    bot = request.app.state.bot
    dp = request.app.state.dp
    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}
