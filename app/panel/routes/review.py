"""Upload review queue (panel): list pending, approve / reject with notify."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.panel.notify import notify_user
from app.services.media_service import MediaService

router = APIRouter()
PAGE_SIZE = 20


@router.get("/review")
async def review_list(
    request: Request,
    page: int = 0,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    service = MediaService(session)
    total = await service.count_pending()
    page = max(0, page)
    items = await service.list_pending(limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    return render(
        request, "review.html", items=items, total=total, page=page, page_size=PAGE_SIZE
    )


@router.post("/review/{media_id}/approve")
async def review_approve(
    request: Request,
    media_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    service = MediaService(session)
    media = await service.approve(media_id, None)
    if media is not None:
        owner_tg = await service.owner_telegram_id(media.owner_user_id)
        await notify_user(
            owner_tg, messages.upload_approved_notify(service.deep_link(media), media.code)
        )
        await audit(session, request, "media_approve", target=str(media_id))
    return RedirectResponse(url=f"{settings.panel_path}/review", status_code=302)


@router.post("/review/{media_id}/reject")
async def review_reject(
    request: Request,
    media_id: int,
    note: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    service = MediaService(session)
    reason = note.strip() or None
    media = await service.reject(media_id, None, note=reason)
    if media is not None:
        owner_tg = await service.owner_telegram_id(media.owner_user_id)
        await notify_user(owner_tg, messages.upload_rejected_notify(reason))
        await audit(session, request, "media_reject", target=str(media_id))
    return RedirectResponse(url=f"{settings.panel_path}/review", status_code=302)
