"""Broadcast — compose text, confirm, enqueue to the worker's Redis queue."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis_client import get_redis
from app.db.session import get_session
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.services import broadcast as broadcast_service

router = APIRouter()


@router.get("/broadcast")
async def broadcast_page(
    request: Request,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    count = await broadcast_service.audience_count(session)
    return render(request, "broadcast.html", audience=count, confirm=False, text="")


@router.post("/broadcast")
async def broadcast_submit(
    request: Request,
    text: str = Form(...),
    confirm: str = Form("0"),
    csrf_token: str = Form(""),
    user=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    text = text.strip()
    count = await broadcast_service.audience_count(session)
    if not text:
        return render(request, "broadcast.html", audience=count, confirm=False, text="")
    if confirm != "1":
        # show a confirmation step
        return render(request, "broadcast.html", audience=count, confirm=True, text=text)

    await broadcast_service.enqueue_text(
        get_redis(), text=text, requested_by=0
    )
    await audit(session, request, "broadcast_enqueue", target=f"{count} users")
    return RedirectResponse(url=f"{settings.panel_path}/broadcast?sent=1", status_code=302)
