"""Admins — list/add/remove Telegram admins (reuses AdminService)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.services.admin_service import AdminService

router = APIRouter()


@router.get("/admins")
async def admins_list(
    request: Request,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    admins = await AdminService(session).list_all()
    return render(request, "admins.html", admins=admins, env_owners=settings.admin_id_list)


@router.post("/admins/add")
async def admin_add(
    request: Request,
    telegram_id: str = Form(...),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    tid = telegram_id.strip()
    if tid.isdigit():
        await AdminService(session).add_admin(int(tid), role="admin")
        await audit(session, request, "admin_add", target=tid)
        bot = getattr(request.app.state, "bot", None)
        if bot is not None:
            # give the new admin the chat-scoped command menu (best-effort)
            from app.bot.commands_menu import push_admin_commands

            await push_admin_commands(bot, session, int(tid))
    return RedirectResponse(url=f"{settings.panel_path}/admins", status_code=302)


@router.post("/admins/{admin_id}/remove")
async def admin_remove(
    request: Request,
    admin_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    service = AdminService(session)
    admin = await service.get(admin_id)
    # cannot remove env owners (they'd be re-seeded anyway)
    if admin is not None and not AdminService.is_env_owner(admin.telegram_id):
        removed_tid = admin.telegram_id
        await service.remove(admin_id)
        await audit(session, request, "admin_remove", target=str(removed_tid))
        bot = getattr(request.app.state, "bot", None)
        if bot is not None:
            # removed admin falls back to the default (user) command menu
            from app.bot.commands_menu import clear_admin_commands

            await clear_admin_commands(bot, removed_tid)
    return RedirectResponse(url=f"{settings.panel_path}/admins", status_code=302)
