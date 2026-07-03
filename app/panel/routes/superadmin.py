"""Platform super-admin surface (F5) — the ONLY cross-tenant panel.

Every route is gated by ``require_superadmin`` (403 for any customer login) and
runs under the explicit ALL_TENANTS context, so it can list/manage every
tenant. Mutating actions are audited (tenant_id NULL = a platform action).
Decrypted bot tokens are NEVER rendered, returned, or logged here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages as bot_messages
from app.core.config import settings
from app.db.session import get_session
from app.models.tenant import Tenant
from app.models.wallet import WalletTransaction
from app.panel.deps import audit, render, require_superadmin, verify_csrf
from app.services.support_service import SupportService, notify_opener
from app.services.tenant_service import TenantService

router = APIRouter()

_TICKET_STATUSES = ("open", "answered", "closed", "all")


def _p(suffix: str = "") -> str:
    return f"{settings.panel_path}/platform{suffix}"


async def _reload_registry(request: Request, tenant_id: int) -> None:
    """Apply a status change to F2's live registry (add/remove the bot)."""
    registry = getattr(request.app.state, "registry", None)
    if registry is not None:
        try:
            await registry.reload(tenant_id)
        except Exception:
            pass


@router.get("/platform")
async def platform_dashboard(
    request: Request,
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    total = await session.scalar(
        select(func.count(Tenant.id)).where(Tenant.id != 1)
    )
    active = await session.scalar(
        select(func.count(Tenant.id)).where(Tenant.id != 1, Tenant.status == "active")
    )
    # revenue = every bot sale/rental charge (all live in the platform ledger)
    revenue = await session.scalar(
        select(func.coalesce(func.sum(func.abs(WalletTransaction.amount)), 0)).where(
            WalletTransaction.type.in_(("bot_purchase", "bot_renewal"))
        )
    )
    return render(
        request, "platform.html",
        total_tenants=int(total or 0), active_tenants=int(active or 0),
        revenue=int(revenue or 0),
    )


@router.get("/platform/tenants")
async def platform_tenants(
    request: Request,
    q: str = "",
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    tenants = await TenantService(session).list_all(q or None)
    # deliberately pass NO token field to the template
    rows = [
        {
            "id": t.id, "bot_username": t.bot_username, "bot_id": t.bot_id,
            "status": t.status, "plan": t.plan, "expires_at": t.expires_at,
            "owner_user_id": t.owner_user_id,
        }
        for t in tenants
    ]
    return render(request, "platform_tenants.html", tenants=rows, q=q)


@router.post("/platform/tenants/{tenant_id}/suspend")
async def platform_suspend(
    request: Request,
    tenant_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if await TenantService(session).set_status(tenant_id, "suspended"):
        await audit(session, request, "tenant_suspend", target=str(tenant_id))
        await _reload_registry(request, tenant_id)  # removes its webhook
    return RedirectResponse(url=_p("/tenants"), status_code=302)


@router.post("/platform/tenants/{tenant_id}/reactivate")
async def platform_reactivate(
    request: Request,
    tenant_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if await TenantService(session).set_status(tenant_id, "active"):
        await audit(session, request, "tenant_reactivate", target=str(tenant_id))
        await _reload_registry(request, tenant_id)  # re-sets its webhook
    return RedirectResponse(url=_p("/tenants"), status_code=302)


@router.post("/platform/tenants/{tenant_id}/extend")
async def platform_extend(
    request: Request,
    tenant_id: int,
    days: int = Form(30),
    csrf_token: str = Form(""),
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if await TenantService(session).extend(tenant_id, max(1, days)):
        await audit(session, request, "tenant_extend", target=str(tenant_id))
    return RedirectResponse(url=_p("/tenants"), status_code=302)


# --- Platform support inbox (reseller -> platform tickets, H2) --------------
@router.get("/platform/support")
async def platform_support(
    request: Request,
    status: str = "open",
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    status = status if status in _TICKET_STATUSES else "open"
    tickets = await SupportService(session).list_by_target("platform", status)
    return render(
        request, "platform_support.html",
        tickets=tickets, status=status, statuses=_TICKET_STATUSES,
    )


@router.get("/platform/support/{ticket_id}")
async def platform_support_detail(
    request: Request,
    ticket_id: int,
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    svc = SupportService(session)
    ticket = await svc.get(ticket_id)
    if ticket is None or ticket.target != "platform":
        return RedirectResponse(url=_p("/support"), status_code=302)
    msgs = await svc.messages(ticket_id)
    return render(request, "ticket_detail.html", ticket=ticket, msgs=msgs, platform=True)


@router.post("/platform/support/{ticket_id}/reply")
async def platform_support_reply(
    request: Request,
    ticket_id: int,
    body: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    body = body.strip()
    svc = SupportService(session)
    ticket = await svc.get(ticket_id)
    if ticket is not None and ticket.target == "platform" and body:
        ticket, _msg = await svc.add_message(ticket_id, "admin", body)
        if ticket is not None:
            await audit(session, request, "platform_ticket_reply", target=str(ticket_id))
            await notify_opener(
                session, ticket,
                bot_messages.support_user_reply_notify(ticket.subject, body),
            )
    return RedirectResponse(url=_p(f"/support/{ticket_id}"), status_code=302)


@router.post("/platform/support/{ticket_id}/close")
async def platform_support_close(
    request: Request,
    ticket_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    svc = SupportService(session)
    ticket = await svc.get(ticket_id)
    if ticket is not None and ticket.target == "platform":
        await svc.close_ticket(ticket_id)
        await audit(session, request, "platform_ticket_close", target=str(ticket_id))
    return RedirectResponse(url=_p("/support"), status_code=302)
