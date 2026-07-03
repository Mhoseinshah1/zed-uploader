"""Payments — list + approve/reject via the SAME PaymentService the bot uses."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages as bot_messages
from app.core.config import settings
from app.db.session import get_session
from app.models.payment import Payment
from app.models.user import User
from app.panel.deps import audit, render, require_role, verify_csrf
from app.services.payment_service import PaymentService

router = APIRouter()


async def _notify(request: Request, session: AsyncSession, user_id: int, text: str) -> None:
    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        return
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is not None:
        try:
            await bot.send_message(user.telegram_id, text)
        except Exception:
            pass


@router.get("/payments")
async def payments_list(
    request: Request,
    status: str = "pending",
    _=Depends(require_role("owner", "finance")),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Payment).order_by(Payment.id.desc())
    if status == "pending":
        stmt = stmt.where(Payment.status == "pending")
    rows = list(await session.scalars(stmt.limit(100)))
    return render(request, "payments.html", payments=rows, status=status)


@router.post("/payments/{payment_id}/approve")
async def payment_approve(
    request: Request,
    payment_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_role("owner", "finance")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    # CentralPay rows are gateway-verified; manual credit is forbidden.
    existing = await PaymentService(session).get(payment_id)
    if existing is None or existing.method != "card":
        return RedirectResponse(url=f"{settings.panel_path}/payments", status_code=302)
    result, payment = await PaymentService(session).approve(payment_id, admin_telegram_id=0)
    if result == "approved" and payment is not None:
        await audit(session, request, "payment_approve", target=str(payment_id))
        await _notify(
            request, session, payment.user_id,
            bot_messages.user_credited(payment.amount),
        )
        try:  # best-effort tenant log (G1); no bot in the request -> logger builds one
            from app.services.tenant_logger import TenantLogger

            await TenantLogger(session).log_payment(
                method="card", amount=payment.amount, status="approved",
            )
        except Exception:
            pass
    return RedirectResponse(url=f"{settings.panel_path}/payments", status_code=302)


@router.post("/payments/{payment_id}/reject")
async def payment_reject(
    request: Request,
    payment_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_role("owner", "finance")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    existing = await PaymentService(session).get(payment_id)
    if existing is None or existing.method != "card":
        return RedirectResponse(url=f"{settings.panel_path}/payments", status_code=302)
    payment = await PaymentService(session).reject(payment_id, admin_telegram_id=0)
    if payment is not None:
        await audit(session, request, "payment_reject", target=str(payment_id))
        await _notify(
            request, session, payment.user_id, bot_messages.USER_PAYMENT_REJECTED
        )
    return RedirectResponse(url=f"{settings.panel_path}/payments", status_code=302)
