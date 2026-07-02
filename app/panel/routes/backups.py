"""Backups — list/trigger/download/delete + guarded restore (panel, CSRF+audit).

Download/delete/restore resolve file paths ONLY from job rows (never from user
input), so there is no path traversal surface. Restore demands the exact dump
filename typed back as confirmation and is destructive by design.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_superadmin, verify_csrf
from app.services.backup_service import (
    DEFAULT_BACKUP_KEEP,
    KEY_BACKUP_KEEP,
    KEY_BACKUP_SCHEDULE,
    BackupService,
    _dsn,
)
from app.services.bot_setting_service import BotSettingService

router = APIRouter()


def _p() -> str:
    return f"{settings.panel_path}/backups"


@router.get("/backups")
async def backups_page(
    request: Request,
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    setting = BotSettingService(session)
    jobs = await BackupService(session).list_jobs()
    return render(
        request, "backups.html",
        jobs=jobs,
        schedule=(await setting.get_raw(KEY_BACKUP_SCHEDULE)) or "off",
        keep=await setting.get_int(KEY_BACKUP_KEEP, DEFAULT_BACKUP_KEEP),
        error=request.query_params.get("error", ""),
    )


@router.post("/backups/trigger")
async def backups_trigger(
    request: Request,
    csrf_token: str = Form(""),
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    job = await BackupService(session).create_job(type_="manual")
    await audit(session, request, "backup_trigger", target=str(job.id))
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/backups/settings")
async def backups_settings(
    request: Request,
    schedule: str = Form("off"),
    keep: int = Form(DEFAULT_BACKUP_KEEP),
    csrf_token: str = Form(""),
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    setting = BotSettingService(session)
    await setting.set(KEY_BACKUP_SCHEDULE, schedule if schedule in ("off", "daily", "weekly") else "off")
    await setting.set(KEY_BACKUP_KEEP, max(1, keep))
    await audit(session, request, "backup_settings")
    return RedirectResponse(url=_p(), status_code=302)


@router.get("/backups/{job_id}/download")
async def backups_download(
    request: Request,
    job_id: int,
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    job = await BackupService(session).get(job_id)
    if job is None or job.status != "success" or not job.file_path:
        return RedirectResponse(url=f"{_p()}?error=notfound", status_code=302)
    if not os.path.exists(job.file_path):
        return RedirectResponse(url=f"{_p()}?error=filegone", status_code=302)
    await audit(session, request, "backup_download", target=str(job_id))
    return FileResponse(
        job.file_path,
        media_type="application/sql",
        filename=os.path.basename(job.file_path),
    )


@router.post("/backups/{job_id}/delete")
async def backups_delete(
    request: Request,
    job_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if await BackupService(session).delete_job(job_id):
        await audit(session, request, "backup_delete", target=str(job_id))
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/backups/{job_id}/restore")
async def backups_restore(
    request: Request,
    job_id: int,
    confirm_filename: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    """DESTRUCTIVE: applies the dump over the live DB. The exact dump filename
    must be typed back; anything else is rejected before any subprocess runs."""
    await verify_csrf(request)
    from app.services import backup_service

    job = await BackupService(session).get(job_id)
    if job is None or job.status != "success" or not job.file_path:
        return RedirectResponse(url=f"{_p()}?error=notfound", status_code=302)
    expected = os.path.basename(job.file_path)
    if confirm_filename.strip() != expected:
        await audit(session, request, "backup_restore_denied", target=str(job_id))
        return RedirectResponse(url=f"{_p()}?error=confirm", status_code=302)
    if not os.path.exists(job.file_path):
        return RedirectResponse(url=f"{_p()}?error=filegone", status_code=302)

    await audit(session, request, "backup_restore", target=expected)
    ok, error = await backup_service.run_pg_restore(_dsn(), job.file_path)
    if not ok:
        return RedirectResponse(url=f"{_p()}?error=restore", status_code=302)
    return RedirectResponse(url=f"{_p()}?error=restored", status_code=302)
