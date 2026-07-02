"""Bot plans (platform pricing for the buy-a-bot flow, F3) — panel CRUD."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_superadmin, verify_csrf
from app.services.bot_plan_service import BotPlanService

router = APIRouter()


def _p(suffix: str = "") -> str:
    return f"{settings.panel_path}/bot-plans{suffix}"


@router.get("/bot-plans")
async def bot_plans_list(
    request: Request,
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    plans = await BotPlanService(session).list_all()
    return render(request, "bot_plans.html", plans=plans)


@router.post("/bot-plans/save")
async def bot_plan_save(
    request: Request,
    key: str = Form(...),
    title: str = Form(...),
    price: int = Form(0),
    duration_days: int = Form(0),
    is_active: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    key = key.strip()
    if key and title.strip():
        await BotPlanService(session).upsert(
            key, title.strip(), max(0, price), max(0, duration_days),
            is_active=(is_active == "on"),
        )
        await audit(session, request, "bot_plan_save", target=key)
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/bot-plans/{key}/delete")
async def bot_plan_delete(
    request: Request,
    key: str,
    csrf_token: str = Form(""),
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    await BotPlanService(session).delete(key)
    await audit(session, request, "bot_plan_delete", target=key)
    return RedirectResponse(url=_p(), status_code=302)
