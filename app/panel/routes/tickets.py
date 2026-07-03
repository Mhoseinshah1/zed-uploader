"""Support tickets — per-tenant admin inbox (H2).

A tenant admin answers their OWN users' tickets (target=tenant_admin). Scoped by
``require_panel_user`` (the login's tenant), so a tenant only ever sees its own
tickets — the guard fails closed otherwise. A reseller's platform-directed
tickets (target=platform) are NOT shown here; they live in the super-admin inbox.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages as bot_messages
from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.services.support_service import SupportService, notify_opener

router = APIRouter()

STATUSES = ("open", "answered", "closed", "all")


@router.get("/tickets")
async def tickets_page(
    request: Request,
    status: str = "open",
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    status = status if status in STATUSES else "open"
    tickets = await SupportService(session).list_by_target("tenant_admin", status)
    return render(
        request, "tickets.html", tickets=tickets, status=status, statuses=STATUSES
    )


@router.get("/tickets/{ticket_id}")
async def ticket_detail(
    request: Request,
    ticket_id: int,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    svc = SupportService(session)
    ticket = await svc.get(ticket_id)
    if ticket is None or ticket.target != "tenant_admin":
        return RedirectResponse(url=f"{settings.panel_path}/tickets", status_code=302)
    msgs = await svc.messages(ticket_id)
    return render(request, "ticket_detail.html", ticket=ticket, msgs=msgs, platform=False)


@router.post("/tickets/{ticket_id}/reply")
async def ticket_reply(
    request: Request,
    ticket_id: int,
    body: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    body = body.strip()
    svc = SupportService(session)
    ticket = await svc.get(ticket_id)
    if ticket is not None and ticket.target == "tenant_admin" and body:
        ticket, _msg = await svc.add_message(ticket_id, "admin", body)
        if ticket is not None:
            await audit(session, request, "ticket_reply", target=str(ticket_id))
            await notify_opener(
                session, ticket,
                bot_messages.support_user_reply_notify(ticket.subject, body),
            )
    return RedirectResponse(
        url=f"{settings.panel_path}/tickets/{ticket_id}", status_code=302
    )


@router.post("/tickets/{ticket_id}/close")
async def ticket_close(
    request: Request,
    ticket_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    svc = SupportService(session)
    ticket = await svc.get(ticket_id)
    if ticket is not None and ticket.target == "tenant_admin":
        await svc.close_ticket(ticket_id)
        await audit(session, request, "ticket_close", target=str(ticket_id))
    return RedirectResponse(url=f"{settings.panel_path}/tickets", status_code=302)
