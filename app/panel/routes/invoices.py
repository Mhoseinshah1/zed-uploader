"""Invoices — per-tenant list + CSV export (H4).

Read-only receipts for a tenant admin (scoped to the login's tenant by
``require_panel_user``; the guard fails closed otherwise). The platform's own
bot-creation/rental invoices live under the platform tenant, so the super-admin
sees them in their own /invoices.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages as bot_messages
from app.db.session import get_session
from app.panel.deps import render, require_panel_user
from app.services.invoice_service import InvoiceService
from app.services.stats_service import rows_to_csv

router = APIRouter()

_CSV_HEADER = ["invoice_no", "kind", "amount", "method", "provider_ref", "user_id", "created_at"]


@router.get("/invoices")
async def invoices_page(
    request: Request,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    invoices = await InvoiceService(session).list_for_tenant()
    return render(request, "invoices.html", invoices=invoices)


@router.get("/invoices/export.csv")
async def invoices_export(
    request: Request,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    invoices = await InvoiceService(session).list_for_tenant()
    rows = [
        (
            inv.invoice_no,
            bot_messages.invoice_kind_fa(inv.kind),
            inv.amount,
            bot_messages.invoice_method_fa(inv.method),
            inv.provider_ref or "",
            inv.user_id,
            inv.created_at.strftime("%Y-%m-%d %H:%M") if inv.created_at else "",
        )
        for inv in invoices
    ]
    body = rows_to_csv(_CSV_HEADER, rows)
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="invoices.csv"'},
    )
