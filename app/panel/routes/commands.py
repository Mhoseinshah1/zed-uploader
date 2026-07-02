"""Bot command menu (panel): edit each scope's list, re-push to Telegram on save."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.services.bot_command_service import (
    DEFAULT_COMMANDS,
    SCOPES,
    BotCommandService,
)

router = APIRouter()


def _redirect(error: str | None = None) -> RedirectResponse:
    suffix = f"?error={error}" if error else ""
    return RedirectResponse(
        url=f"{settings.panel_path}/commands{suffix}", status_code=302
    )


async def _repush(request: Request, session: AsyncSession, scope: str) -> None:
    """Push the edited list to Telegram right away (best-effort, never fails a save)."""
    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        return
    from app.bot.commands_menu import push_admin_commands_many, push_default_commands
    from app.services.admin_service import AdminService

    if scope == "user":
        await push_default_commands(bot, session)
    else:
        chat_ids = await AdminService.admin_telegram_ids(session)
        await push_admin_commands_many(bot, session, chat_ids)


@router.get("/commands")
async def commands_page(
    request: Request,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    svc = BotCommandService(session)
    scopes = []
    for scope in SCOPES:
        scopes.append(
            {
                "scope": scope,
                "rows": await svc.list_rows(scope),
                "defaults": DEFAULT_COMMANDS[scope],
            }
        )
    return render(
        request, "commands.html",
        scopes=scopes, error=request.query_params.get("error"),
    )


@router.post("/commands/{scope}/seed")
async def commands_seed(
    request: Request,
    scope: str,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    """Materialize the built-in defaults as editable rows."""
    await verify_csrf(request)
    if scope in SCOPES and await BotCommandService(session).seed_defaults(scope):
        await audit(session, request, "commands_seed", target=scope)
    return _redirect()


@router.post("/commands/{scope}/reset")
async def commands_reset(
    request: Request,
    scope: str,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    """Delete every row of the scope -> the built-in defaults apply again."""
    await verify_csrf(request)
    if scope in SCOPES:
        await BotCommandService(session).reset(scope)
        await audit(session, request, "commands_reset", target=scope)
        await _repush(request, session, scope)
    return _redirect()


@router.post("/commands/{scope}/add")
async def commands_add(
    request: Request,
    scope: str,
    command: str = Form(""),
    description: str = Form(""),
    sort_order: int = Form(0),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    entry = None
    if scope in SCOPES:
        entry = await BotCommandService(session).upsert(
            scope, command, description, sort_order
        )
    if entry is None:
        return _redirect(error="invalid")
    await audit(session, request, "commands_add", target=f"{scope}:{entry.command}")
    await _repush(request, session, scope)
    return _redirect()


@router.post("/commands/{entry_id}/save")
async def commands_save(
    request: Request,
    entry_id: int,
    description: str = Form(""),
    sort_order: int = Form(0),
    is_active: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    entry = await BotCommandService(session).update(
        entry_id, description, sort_order, bool(is_active)
    )
    if entry is None:
        return _redirect(error="invalid")
    await audit(
        session, request, "commands_save", target=f"{entry.scope}:{entry.command}"
    )
    await _repush(request, session, entry.scope)
    return _redirect()


@router.post("/commands/{entry_id}/delete")
async def commands_delete(
    request: Request,
    entry_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    entry = await BotCommandService(session).remove(entry_id)
    if entry is not None:
        await audit(
            session, request, "commands_delete", target=f"{entry.scope}:{entry.command}"
        )
        await _repush(request, session, entry.scope)
    return _redirect()
