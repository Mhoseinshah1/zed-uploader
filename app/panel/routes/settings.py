"""Settings — card, default protect/auto-delete, force-join channels."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.services.bot_setting_service import (
    KEY_AUTODELETE,
    KEY_CARD_HOLDER,
    KEY_CARD_NUMBER,
    KEY_PROTECT,
    BotSettingService,
)
from app.services.channel_service import ChannelService

router = APIRouter()


def _p(path: str) -> str:
    return f"{app_settings.panel_path}{path}"


@router.get("/settings")
async def settings_page(
    request: Request,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    setting = BotSettingService(session)
    channels = await ChannelService(session).list_all()
    ctx = {
        "card_number": await setting.get_raw(KEY_CARD_NUMBER) or "",
        "card_holder": await setting.get_raw(KEY_CARD_HOLDER) or "",
        "default_protect": await setting.effective_protect(),
        "default_autodelete": await setting.effective_autodelete(),
        "channels": channels,
    }
    return render(request, "settings.html", **ctx)


@router.post("/settings/card")
async def settings_card(
    request: Request,
    card_number: str = Form(""),
    card_holder: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    setting = BotSettingService(session)
    await setting.set(KEY_CARD_NUMBER, card_number.strip())
    await setting.set(KEY_CARD_HOLDER, card_holder.strip())
    await audit(session, request, "settings_card")
    return RedirectResponse(url=_p("/settings"), status_code=302)


@router.post("/settings/defaults")
async def settings_defaults(
    request: Request,
    default_protect: str = Form(""),
    default_autodelete: int = Form(0),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    setting = BotSettingService(session)
    await setting.set(KEY_PROTECT, default_protect == "on")
    await setting.set(KEY_AUTODELETE, max(0, default_autodelete))
    await audit(session, request, "settings_defaults")
    return RedirectResponse(url=_p("/settings"), status_code=302)


@router.post("/settings/channels/add")
async def channel_add(
    request: Request,
    chat_id: str = Form(...),
    title: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    chat = chat_id.strip()
    if chat:
        await ChannelService(session).add(chat, title=title.strip() or None)
        await audit(session, request, "channel_add", target=chat)
    return RedirectResponse(url=_p("/settings"), status_code=302)


@router.post("/settings/channels/{channel_id}/toggle")
async def channel_toggle(
    request: Request,
    channel_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    await ChannelService(session).toggle(channel_id)
    await audit(session, request, "channel_toggle", target=str(channel_id))
    return RedirectResponse(url=_p("/settings"), status_code=302)


@router.post("/settings/channels/{channel_id}/remove")
async def channel_remove(
    request: Request,
    channel_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    await ChannelService(session).remove(channel_id)
    await audit(session, request, "channel_remove", target=str(channel_id))
    return RedirectResponse(url=_p("/settings"), status_code=302)
