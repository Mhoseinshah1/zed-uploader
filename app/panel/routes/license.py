"""License — status/expiry/fingerprint page + activate action (panel).

Platform-owner only (H1): the license governs the whole installation (a single
global row), so both routes are gated to ``require_superadmin``. A reseller /
customer panel user gets 403 and can never view or change licensing.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_superadmin, verify_csrf
from app.services.license_service import (
    LicenseService,
    evaluate,
    licensing_bypassed,
    server_fingerprint,
)

router = APIRouter()


@router.get("/license")
async def license_page(
    request: Request,
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    row = await LicenseService(session).get_row()
    return render(
        request, "license.html",
        bypassed=licensing_bypassed(),
        row=row,
        paid_allowed=licensing_bypassed() or evaluate(row),
        fingerprint=server_fingerprint(),
        grace_days=settings.license_grace_days,
        result=request.query_params.get("result", ""),
    )


@router.post("/license/activate")
async def license_activate(
    request: Request,
    key: str = Form(...),
    csrf_token: str = Form(""),
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    result = "invalid"
    if key.strip():
        result = await LicenseService(session).activate(key)
        await audit(session, request, "license_activate", target=result)  # never the key
    return RedirectResponse(
        url=f"{settings.panel_path}/license?result={result}", status_code=302
    )
