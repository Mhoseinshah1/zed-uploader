"""Public ad click-through: count the click, then 302 to the ad's URL.

Cheap and best-effort by design — rate-limited, no auth (it's a public
redirect); an unknown/buttonless ad falls back to the bot's chat link.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import DbSession, RateLimitDep
from app.core.config import settings
from app.core.logging import get_logger
from app.core.tenant_context import all_tenants, reset_tenant, set_tenant
from app.models.ad import Ad
from app.services.ad_service import AdService

router = APIRouter(tags=["ads"])
log = get_logger("ads")

_FALLBACK = f"https://t.me/{settings.bot_username}"


@router.get("/ad/{ad_id}/click", dependencies=[RateLimitDep])
async def ad_click(ad_id: int, session: DbSession) -> RedirectResponse:
    # No tenant context on a public click: resolve the ad's tenant across all
    # tenants, then record the click scoped to THAT tenant (each ad belongs to
    # one tenant). Unknown ad -> fallback link, never a cross-tenant write.
    with all_tenants():
        tenant_id = await session.scalar(select(Ad.tenant_id).where(Ad.id == ad_id))
    if tenant_id is None:
        return RedirectResponse(url=_FALLBACK, status_code=302)
    ctx = set_tenant(tenant_id)
    try:
        url = await AdService(session).record_click(ad_id)
    finally:
        reset_tenant(ctx)
    return RedirectResponse(url=url or _FALLBACK, status_code=302)
