"""Broadcast — compose text, confirm, enqueue; list jobs + retry failed."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.services import broadcast as broadcast_service

router = APIRouter()


def _filter(value: str) -> str:
    return value if value in broadcast_service.AUDIENCE_FILTERS else "all"


@router.get("/broadcast")
async def broadcast_page(
    request: Request,
    audience_filter: str = "all",
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    af = _filter(audience_filter)
    count = await broadcast_service.audience_count(session, af)
    jobs = await broadcast_service.list_jobs(session)
    return render(
        request, "broadcast.html", audience=count, confirm=False, text="",
        jobs=jobs, audience_filter=af, filters=broadcast_service.AUDIENCE_FILTERS,
    )


@router.post("/broadcast")
async def broadcast_submit(
    request: Request,
    text: str = Form(...),
    confirm: str = Form("0"),
    audience_filter: str = Form("all"),
    csrf_token: str = Form(""),
    user=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    from app.services.license_service import paid_features_allowed

    af = _filter(audience_filter)
    if not await paid_features_allowed(session):
        return RedirectResponse(
            url=f"{settings.panel_path}/broadcast?error=license", status_code=302
        )
    text = text.strip()
    count = await broadcast_service.audience_count(session, af)
    ctx = dict(audience=count, audience_filter=af, filters=broadcast_service.AUDIENCE_FILTERS)
    if not text:
        jobs = await broadcast_service.list_jobs(session)
        return render(request, "broadcast.html", confirm=False, text="", jobs=jobs, **ctx)
    if confirm != "1":
        jobs = await broadcast_service.list_jobs(session)
        return render(request, "broadcast.html", confirm=True, text=text, jobs=jobs, **ctx)

    job = await broadcast_service.create_job(session, text=text, created_by=None, plan_filter=af)
    await audit(
        session, request, "broadcast_enqueue",
        target=f"job:{job.id} ({job.total} users, {af})",
    )
    return RedirectResponse(url=f"{settings.panel_path}/broadcast?sent=1", status_code=302)


@router.post("/broadcast/{job_id}/retry")
async def broadcast_retry(
    request: Request,
    job_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    requeued = await broadcast_service.retry_failed(session, job_id)
    await audit(session, request, "broadcast_retry", target=f"job:{job_id} ({requeued} rows)")
    return RedirectResponse(
        url=f"{settings.panel_path}/broadcast?retried={requeued}", status_code=302
    )
