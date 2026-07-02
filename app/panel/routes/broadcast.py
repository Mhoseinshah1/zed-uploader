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


@router.get("/broadcast")
async def broadcast_page(
    request: Request,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    count = await broadcast_service.audience_count(session)
    jobs = await broadcast_service.list_jobs(session)
    return render(
        request, "broadcast.html", audience=count, confirm=False, text="", jobs=jobs
    )


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
    from app.services.license_service import paid_features_allowed

    if not await paid_features_allowed(session):
        return RedirectResponse(
            url=f"{settings.panel_path}/broadcast?error=license", status_code=302
        )
    text = text.strip()
    count = await broadcast_service.audience_count(session)
    if not text:
        jobs = await broadcast_service.list_jobs(session)
        return render(
            request, "broadcast.html", audience=count, confirm=False, text="", jobs=jobs
        )
    if confirm != "1":
        # show a confirmation step
        jobs = await broadcast_service.list_jobs(session)
        return render(
            request, "broadcast.html", audience=count, confirm=True, text=text, jobs=jobs
        )

    job = await broadcast_service.create_job(session, text=text, created_by=None)
    await audit(session, request, "broadcast_enqueue", target=f"job:{job.id} ({job.total} users)")
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
