"""Editable bot texts (panel): override any user-facing key, default as preview."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.services.bot_setting_service import BotSettingService
from app.services.text_service import OVERRIDABLE_TEXTS, _setting_key, set_text

router = APIRouter()


@router.get("/texts")
async def texts_page(
    request: Request,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    setting = BotSettingService(session)
    rows = []
    for key, default in OVERRIDABLE_TEXTS.items():
        override = await setting.get_raw(_setting_key(key)) or ""
        rows.append({"key": key, "default": default, "override": override})
    return render(request, "texts.html", rows=rows)


@router.post("/texts/{key}")
async def texts_save(
    request: Request,
    key: str,
    value: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if key in OVERRIDABLE_TEXTS:
        await set_text(session, key, value)  # '' clears -> default; cache busted
        await audit(session, request, "text_save", target=key)
    return RedirectResponse(url=f"{settings.panel_path}/texts", status_code=302)
