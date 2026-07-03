"""Feature flags — panel UI (owner-only, I5).

Edit each feature's enabled state + minimum plan; a change actually drives
FeatureService gating (protect_content / auto_delete / batch_upload). Every
change is audited and tenant-scoped by the guard.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.models.plan import Plan
from app.panel.deps import audit, render, require_role, verify_csrf
from app.services.feature_service import FEATURE_KEYS, FeatureService

router = APIRouter()


@router.get("/features")
async def features_page(
    request: Request,
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    flags = await FeatureService.list_flags(session, FEATURE_KEYS)
    plans = list(await session.scalars(select(Plan).order_by(Plan.id)))
    items = [{"key": k, "flag": flags.get(k)} for k in FEATURE_KEYS]
    return render(request, "features.html", items=items, plans=plans)


@router.post("/features/{key}")
async def features_save(
    request: Request,
    key: str,
    is_enabled: str = Form(""),
    plan: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if key in FEATURE_KEYS:
        enabled = is_enabled == "on"
        await FeatureService.set_flag(session, key, enabled, plan.strip() or None)
        await audit(session, request, "feature_flag", target=f"{key}:{'on' if enabled else 'off'}")
    return RedirectResponse(url=f"{settings.panel_path}/features", status_code=302)
