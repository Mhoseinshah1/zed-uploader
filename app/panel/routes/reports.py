"""Abuse reports — list (by status) + review actions (panel, CSRF+audit)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.models.media import Media
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.services.report_service import ReportService

router = APIRouter()

STATUSES = ("pending", "reviewed", "dismissed")


@router.get("/reports")
async def reports_page(
    request: Request,
    status: str = "pending",
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    status = status if status in STATUSES else "pending"
    reports = await ReportService(session).list_reports(status=status)
    media_ids = {r.media_id for r in reports}
    media_map = {}
    if media_ids:
        rows = await session.scalars(select(Media).where(Media.id.in_(media_ids)))
        media_map = {m.id: m for m in rows}
    return render(
        request, "reports.html", reports=reports, status=status,
        statuses=STATUSES, media_map=media_map,
    )


@router.post("/reports/{report_id}/deactivate")
async def reports_deactivate(
    request: Request,
    report_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if await ReportService(session).review_deactivate(report_id, None):
        await audit(session, request, "report_deactivate", target=str(report_id))
    return RedirectResponse(url=f"{settings.panel_path}/reports", status_code=302)


@router.post("/reports/{report_id}/dismiss")
async def reports_dismiss(
    request: Request,
    report_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if await ReportService(session).dismiss(report_id, None):
        await audit(session, request, "report_dismiss", target=str(report_id))
    return RedirectResponse(url=f"{settings.panel_path}/reports", status_code=302)
