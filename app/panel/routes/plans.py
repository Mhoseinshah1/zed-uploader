"""Plans — edit price/duration/max_files, toggle active (reuses PlanService)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_role, verify_csrf
from app.services.plan_service import PlanService

router = APIRouter()


@router.get("/plans")
async def plans_list(
    request: Request,
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    plans = await PlanService(session).list_all()
    return render(request, "plans.html", plans=plans)


@router.post("/plans/{key}/update")
async def plan_update(
    request: Request,
    key: str,
    price: int = Form(...),
    duration_days: int = Form(...),
    max_files: str = Form(""),
    stars_price: str = Form(""),
    is_active: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    service = PlanService(session)
    await service.set_price(key, max(0, price))
    await service.set_duration(key, max(0, duration_days))
    max_files_value = int(max_files) if max_files.strip().isdigit() else None
    await service.set_max_files(key, max_files_value)
    stars_value = int(stars_price) if stars_price.strip().isdigit() else None
    await service.set_stars_price(key, stars_value)
    await service.set_active(key, is_active == "on")
    await audit(session, request, "plan_update", target=key)
    return RedirectResponse(url=f"{settings.panel_path}/plans", status_code=302)
