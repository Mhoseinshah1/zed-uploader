"""Public ad click-through: count the click, then 302 to the ad's URL.

Cheap and best-effort by design — rate-limited, no auth (it's a public
redirect); an unknown/buttonless ad falls back to the bot's chat link.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.api.deps import DbSession, RateLimitDep
from app.core.config import settings
from app.core.logging import get_logger
from app.services.ad_service import AdService

router = APIRouter(tags=["ads"])
log = get_logger("ads")


@router.get("/ad/{ad_id}/click", dependencies=[RateLimitDep])
async def ad_click(ad_id: int, session: DbSession) -> RedirectResponse:
    url = await AdService(session).record_click(ad_id)
    if url is None:
        url = f"https://t.me/{settings.bot_username}"
    return RedirectResponse(url=url, status_code=302)
