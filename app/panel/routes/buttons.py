"""Custom bot menu buttons (J8): list / create / toggle / delete.

Creation goes through ``CustomButtonService.create`` which enforces the type
set, the ``ACTION_WHITELIST`` for action buttons, and the URL scheme —
arbitrary behaviors can never be stored.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.models.custom_button import BUTTON_TYPES
from app.panel.deps import audit, render, require_role, verify_csrf
from app.services.custom_button_service import ACTION_WHITELIST, CustomButtonService

router = APIRouter()


def _p(suffix: str = "") -> str:
    return f"{settings.panel_path}/buttons{suffix}"


@router.get("/buttons")
async def buttons_page(
    request: Request,
    _=Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(get_session),
):
    return render(
        request, "buttons.html",
        items=await CustomButtonService(session).list_all(),
        types=BUTTON_TYPES, actions=ACTION_WHITELIST,
        error=request.query_params.get("error", ""),
    )


@router.post("/buttons/create")
async def button_create(
    request: Request,
    label: str = Form(...),
    type: str = Form(...),
    value: str = Form(...),
    sort_order: int = Form(0),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    button = await CustomButtonService(session).create(
        label, type, value, sort_order=sort_order
    )
    if button is None:
        return RedirectResponse(url=_p("?error=invalid"), status_code=302)
    await audit(session, request, "button_create", target=f"{button.id}:{button.label}")
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/buttons/{button_id}/toggle")
async def button_toggle(
    request: Request,
    button_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if await CustomButtonService(session).toggle(button_id):
        await audit(session, request, "button_toggle", target=str(button_id))
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/buttons/{button_id}/delete")
async def button_delete(
    request: Request,
    button_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if await CustomButtonService(session).delete(button_id):
        await audit(session, request, "button_delete", target=str(button_id))
    return RedirectResponse(url=_p(), status_code=302)
