"""Ads — owner CRUD + impression/click counts (panel)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.models.ad import PLACEMENTS
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.services.ad_service import AdService

router = APIRouter()


def _p() -> str:
    return f"{settings.panel_path}/ads"


def _limit(raw: str) -> int | None:
    raw = raw.strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


@router.get("/ads")
async def ads_page(
    request: Request,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    ads = await AdService(session).list_all()
    return render(request, "ads.html", ads=ads, placements=PLACEMENTS)


@router.post("/ads/create")
async def ads_create(
    request: Request,
    title: str = Form(...),
    text: str = Form(...),
    placement: str = Form(...),
    button_text: str = Form(""),
    button_url: str = Form(""),
    target_plan: str = Form(""),
    impression_limit: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if title.strip() and text.strip() and placement in PLACEMENTS:
        ad = await AdService(session).create(
            title=title, text=text, placement=placement,
            button_text=button_text, button_url=button_url,
            target_plan=target_plan, impression_limit=_limit(impression_limit),
        )
        await audit(session, request, "ad_create", target=str(ad.id))
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/ads/{ad_id}/toggle")
async def ads_toggle(
    request: Request,
    ad_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if await AdService(session).toggle(ad_id):
        await audit(session, request, "ad_toggle", target=str(ad_id))
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/ads/{ad_id}/delete")
async def ads_delete(
    request: Request,
    ad_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if await AdService(session).delete(ad_id):
        await audit(session, request, "ad_delete", target=str(ad_id))
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/ads/{ad_id}/edit")
async def ads_edit(
    request: Request,
    ad_id: int,
    title: str = Form(...),
    text: str = Form(...),
    placement: str = Form(...),
    button_text: str = Form(""),
    button_url: str = Form(""),
    target_plan: str = Form(""),
    impression_limit: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if title.strip() and text.strip() and placement in PLACEMENTS:
        ok = await AdService(session).update_fields(
            ad_id,
            title=title.strip(),
            text=text,
            placement=placement,
            button_text=button_text.strip() or None,
            button_url=button_url.strip() or None,
            target_plan=target_plan.strip() or None,
            impression_limit=_limit(impression_limit),
        )
        if ok:
            await audit(session, request, "ad_edit", target=str(ad_id))
    return RedirectResponse(url=_p(), status_code=302)
