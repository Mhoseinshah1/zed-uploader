"""Per-tenant Telegram webhook (Phase F2): POST /tenant/{bot_id}/webhook.

Resolves the bot from the in-memory registry (no DB on the hot path), validates
that tenant's secret token, sets the tenant context for the update's lifetime
(via ``feed_update(..., tenant_id=...)`` -> TenantContextMiddleware), and feeds
it into the shared dispatcher bound to that tenant's Bot. An unknown bot or a
wrong/missing secret is rejected before any dispatch.
"""
from __future__ import annotations

from aiogram.types import Update
from fastapi import APIRouter, HTTPException, Request, status

from app.core.logging import get_logger

router = APIRouter(tags=["telegram"])
log = get_logger("tenant_webhook")


@router.post("/tenant/{bot_id}/webhook")
async def tenant_webhook(bot_id: int, request: Request) -> dict[str, bool]:
    registry = getattr(request.app.state, "registry", None)
    entry = registry.get(bot_id) if registry is not None else None
    if entry is None:
        # unknown or suspended bot — do not leak which
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown bot")

    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not entry.secret or secret != entry.secret:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid secret token")

    dp = request.app.state.dp
    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": entry.bot})
    # tenant_id flows to TenantContextMiddleware, scoping the whole update.
    await dp.feed_update(entry.bot, update, tenant_id=entry.tenant_id)
    return {"ok": True}
