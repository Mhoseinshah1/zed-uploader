"""Settings — card, default protect/auto-delete, force-join channels."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_role, verify_csrf
from app.services.bot_setting_service import (
    DEFAULT_TOPUP_MIN,
    KEY_AUTODELETE,
    KEY_CAPTION_SIGNATURE,
    KEY_CAPTION_STRIP_LINKS,
    KEY_FREE_DAILY_QUOTA,
    KEY_PREVIEW_CHANNEL_ID,
    KEY_PREVIEW_ENABLED,
    KEY_CARD_ENABLED,
    KEY_CARD_HOLDER,
    KEY_CARD_NUMBER,
    KEY_PROTECT,
    KEY_PUBLIC_SEARCH_ENABLED,
    KEY_TOPUP_MIN,
    KEY_USER_UPLOAD_ENABLED,
    KEY_USER_UPLOAD_REVIEW,
    BotSettingService,
)
from app.services.channel_service import ChannelService

router = APIRouter()


def _p(path: str) -> str:
    return f"{app_settings.panel_path}{path}"


@router.get("/settings")
async def settings_page(
    request: Request,
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    setting = BotSettingService(session)
    channels = await ChannelService(session).list_all()
    ctx = {
        "card_number": await setting.get_raw(KEY_CARD_NUMBER) or "",
        "card_holder": await setting.get_raw(KEY_CARD_HOLDER) or "",
        "default_protect": await setting.effective_protect(),
        "default_autodelete": await setting.effective_autodelete(),
        "user_upload_enabled": await setting.user_upload_enabled(),
        "user_upload_requires_review": await setting.user_upload_requires_review(),
        "public_search_enabled": await setting.public_search_enabled(),
        "card_enabled": await setting.card_enabled(),
        "topup_min": await setting.get_int(KEY_TOPUP_MIN, DEFAULT_TOPUP_MIN),
        "free_daily_quota": await setting.get_int(KEY_FREE_DAILY_QUOTA, 0),
        "caption_strip_links": await setting.get_bool(KEY_CAPTION_STRIP_LINKS, False),
        "caption_signature": await setting.get_raw(KEY_CAPTION_SIGNATURE) or "",
        "preview_enabled": await setting.get_bool(KEY_PREVIEW_ENABLED, False),
        "preview_channel_id": await setting.get_raw(KEY_PREVIEW_CHANNEL_ID) or "",
        "channels": channels,
    }
    return render(request, "settings.html", **ctx)


@router.post("/settings/preview")
async def settings_preview(
    request: Request,
    preview_enabled: str = Form(""),
    preview_channel_id: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    """J5: channel preview auto-post — toggle + channel id."""
    await verify_csrf(request)
    setting = BotSettingService(session)
    await setting.set(KEY_PREVIEW_ENABLED, preview_enabled == "on")
    await setting.set(KEY_PREVIEW_CHANNEL_ID, preview_channel_id.strip())
    await audit(session, request, "settings_preview")
    return RedirectResponse(url=_p("/settings"), status_code=302)


@router.post("/settings/caption")
async def settings_caption(
    request: Request,
    caption_strip_links: str = Form(""),
    caption_signature: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    """J3: caption tools — strip links/mentions + optional signature."""
    await verify_csrf(request)
    setting = BotSettingService(session)
    await setting.set(KEY_CAPTION_STRIP_LINKS, caption_strip_links == "on")
    await setting.set(KEY_CAPTION_SIGNATURE, caption_signature.strip())
    await audit(session, request, "settings_caption")
    return RedirectResponse(url=_p("/settings"), status_code=302)


@router.post("/settings/payments")
async def settings_payments(
    request: Request,
    card_enabled: str = Form(""),
    topup_min: int = Form(DEFAULT_TOPUP_MIN),
    free_daily_quota: int = Form(0),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    """I6: card top-up on/off (independent of the card number) + minimum top-up."""
    await verify_csrf(request)
    setting = BotSettingService(session)
    await setting.set(KEY_CARD_ENABLED, card_enabled == "on")
    await setting.set(KEY_TOPUP_MIN, max(0, topup_min))
    await setting.set(KEY_FREE_DAILY_QUOTA, max(0, free_daily_quota))  # J6
    await audit(session, request, "settings_payments")
    return RedirectResponse(url=_p("/settings"), status_code=302)


@router.post("/settings/card")
async def settings_card(
    request: Request,
    card_number: str = Form(""),
    card_holder: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner")),
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
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    setting = BotSettingService(session)
    await setting.set(KEY_PROTECT, default_protect == "on")
    await setting.set(KEY_AUTODELETE, max(0, default_autodelete))
    await audit(session, request, "settings_defaults")
    return RedirectResponse(url=_p("/settings"), status_code=302)


@router.post("/settings/uploads")
async def settings_uploads(
    request: Request,
    user_upload_enabled: str = Form(""),
    user_upload_requires_review: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    setting = BotSettingService(session)
    await setting.set(KEY_USER_UPLOAD_ENABLED, user_upload_enabled == "on")
    await setting.set(KEY_USER_UPLOAD_REVIEW, user_upload_requires_review == "on")
    await audit(session, request, "settings_uploads")
    return RedirectResponse(url=_p("/settings"), status_code=302)


@router.post("/settings/search")
async def settings_search(
    request: Request,
    public_search_enabled: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    setting = BotSettingService(session)
    await setting.set(KEY_PUBLIC_SEARCH_ENABLED, public_search_enabled == "on")
    await audit(session, request, "settings_search")
    # the user command menu advertises /search only while search is enabled —
    # refresh the resolved list and re-push the default scope (best-effort)
    from app.services.bot_command_service import bust_cache

    await bust_cache("user")
    bot = getattr(request.app.state, "bot", None)
    if bot is not None:
        from app.bot.commands_menu import push_default_commands

        await push_default_commands(bot, session)
    return RedirectResponse(url=_p("/settings"), status_code=302)


@router.post("/settings/channels/add")
async def channel_add(
    request: Request,
    chat_id: str = Form(...),
    title: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner")),
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
    _=Depends(require_role("owner")),
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
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    await ChannelService(session).remove(channel_id)
    await audit(session, request, "channel_remove", target=str(channel_id))
    return RedirectResponse(url=_p("/settings"), status_code=302)
