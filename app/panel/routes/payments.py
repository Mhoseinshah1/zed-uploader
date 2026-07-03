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


_METHODS = ("card", "zarinpal", "zibal", "centralpay", "telegram_stars")
_FIN = ("owner", "finance")


@router.get("/payments")
async def payments_list(
    request: Request,
    status: str = "pending",
    method: str = "",
    user_id: str = "",
    _=Depends(require_role(*_FIN)),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Payment).order_by(Payment.id.desc())
    if status in ("pending", "approved", "rejected", "refunded", "expired"):
        stmt = stmt.where(Payment.status == status)
    method = method.strip()
    if method in _METHODS:
        stmt = stmt.where(Payment.method == method)
    uid = int(user_id) if user_id.strip().isdigit() else None
    if uid is not None:
        stmt = stmt.where(Payment.user_id == uid)
    rows = list(await session.scalars(stmt.limit(100)))
    return render(
        request, "payments.html", payments=rows, status=status,
        method=method, user_id=user_id, methods=_METHODS,
    )


@router.get("/payments/{payment_id}")
async def payment_detail(
    request: Request,
    payment_id: int,
    _=Depends(require_role(*_FIN)),
    session: AsyncSession = Depends(get_session),
):
    payment = await PaymentService(session).get(payment_id)
    if payment is None:
        return RedirectResponse(url=f"{settings.panel_path}/payments", status_code=302)
    return render(
        request, "payment_detail.html", payment=payment,
        is_gateway=payment.method != "card",
    )


@router.post("/payments/{payment_id}/recheck")
async def payment_recheck(
    request: Request,
    payment_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_role(*_FIN)),
    session: AsyncSession = Depends(get_session),
):
    """I6: re-verify a GATEWAY payment via the existing idempotent verify. A card
    payment resolves to no provider -> 'failed' (this never manually credits)."""
    await verify_csrf(request)
    from app.services.providers import verify_order

    result = await verify_order(session, payment_id)  # idempotent
    await audit(session, request, "payment_recheck", target=f"{payment_id}:{result}")
    return RedirectResponse(
        url=f"{settings.panel_path}/payments/{payment_id}?msg={result}", status_code=302
    )


@router.post("/payments/reconcile")
async def payments_reconcile(
    request: Request,
    csrf_token: str = Form(""),
    _=Depends(require_role(*_FIN)),
    session: AsyncSession = Depends(get_session),
):
    """L1: batch re-verify pending gateway payments via the idempotent verify,
    expiring stale unpaid ones. Never double-credits (verify locks + checks)."""
    await verify_csrf(request)
    from app.services.reconcile_service import reconcile_pending

    report = await reconcile_pending(session)
    await audit(
        session, request, "payments_reconcile",
        target=",".join(f"{k}={v}" for k, v in report.items()),
    )
    query = "&".join(f"r_{k}={v}" for k, v in report.items())
    return RedirectResponse(
        url=f"{settings.panel_path}/payments?reconciled=1&{query}", status_code=302
    )


@router.post("/payments/{payment_id}/refund")
async def payment_refund(
    request: Request,
    payment_id: int,
    reason: str = Form(""),
    csrf_token: str = Form(""),
    panel_user=Depends(require_role(*_FIN)),
    session: AsyncSession = Depends(get_session),
):
    """L1: reverse a settled payment exactly once (see RefundService policy)."""
    await verify_csrf(request)
    from app.services.refund_service import REFUNDED, RefundService

    result = await RefundService(session).refund(
        payment_id, panel_user_id=panel_user.id, reason=reason
    )
    await audit(session, request, "payment_refund", target=f"{payment_id}:{result}")
    if result == REFUNDED:
        payment = await PaymentService(session).get(payment_id)
        if payment is not None:
            await _notify(
                request, session, payment.user_id,
                bot_messages.payment_refunded_notify(payment.amount),
            )
    return RedirectResponse(
        url=f"{settings.panel_path}/payments/{payment_id}?msg={result}",
        status_code=302,
    )


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
