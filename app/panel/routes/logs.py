"""Log group setup (G1) — connect the tenant's Telegram forum supergroup.

Tenant-scoped (require_panel_user binds the login's tenant). Topics are created
lazily by the bot on the first event, so this page only stores the group id.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.services.tenant_logger import TenantLogger

router = APIRouter()


@router.get("/logs")
async def logs_page(
    request: Request,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    row = await TenantLogger(session).get_settings()
    return render(request, "logs.html", settings=row)


@router.post("/logs")
async def logs_save(
    request: Request,
    log_group_id: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    raw = log_group_id.strip()
    group_id = int(raw) if raw.lstrip("-").isdigit() else None
    await TenantLogger(session).set_group(group_id)
    await audit(session, request, "log_group_set", target=str(group_id))
    return RedirectResponse(url=f"{settings.panel_path}/logs", status_code=302)
