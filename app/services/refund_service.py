"""RefundService (L1) — reverse a settled payment, exactly once.

REFUND POLICY (documented):

* Only an ``approved`` (settled) payment can be refunded, and at most once:
  the row is re-loaded FOR UPDATE and the status re-checked, so a double
  submit (or two concurrent operators) can never double-reverse. ``refunded``
  is terminal.
* The reversal compensates what the payment DELIVERED — always through
  ``WalletService`` (a ledger entry), never a raw balance edit:

  - **top-up** (intent ``topup``/NULL): the credited amount is debited back
    as one ``refund`` ledger entry. If the user has already spent it, the
    refund is REFUSED (``insufficient``) — the balance never goes negative
    and there are no partial refunds; the operator resolves such cases
    manually via the audited wallet-adjust tools.
  - **plan purchase** (intent ``plan:<key>``): the deposit was consumed by
    the plan purchase in the same flow (net wallet change zero), so the
    refund REVOKES the plan instead (only if the user still holds that exact
    plan — a later different plan is left alone) and moves no money.

* Returning the actual funds to the customer happens OUTSIDE the system
  (gateway-side / card transfer) — this records the internal reversal.
* A ``kind="refund"`` credit-note invoice is recorded best-effort AFTER the
  money commit (idempotent on ``refund:payment:<id>``).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.payment import Payment
from app.models.subscription import Subscription
from app.models.user import User
from app.services.wallet_service import InsufficientFunds, WalletService

log = get_logger("refund")

REFUNDED = "refunded"
ALREADY = "already"
NOT_SETTLED = "not_settled"
NOT_FOUND = "not_found"
INSUFFICIENT = "insufficient"


class RefundService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def refund(
        self, payment_id: int, *, panel_user_id: int, reason: str
    ) -> str:
        payment = await self.session.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
        if payment is None:
            return NOT_FOUND
        if payment.status == "refunded":
            return ALREADY  # terminal — a double submit never double-reverses
        if payment.status != "approved":
            return NOT_SETTLED

        # snapshot before any rollback/commit can expire the instance
        user_id, amount = payment.user_id, payment.amount
        method, provider_ref = payment.method, payment.provider_ref
        plan_key: str | None = None
        if payment.intent and payment.intent.startswith("plan:"):
            plan_key = payment.intent.split(":", 1)[1]

        if plan_key is None:
            # top-up: one compensating ledger entry (no commit yet — the debit
            # and the status flip must land atomically)
            try:
                await WalletService(self.session).debit_nocommit(
                    user_id,
                    amount,
                    ttype="refund",
                    reference=f"refund:payment:{payment_id}",
                    description=(reason or "")[:255] or None,
                )
            except InsufficientFunds:
                await self.session.rollback()
                log.warning(
                    "refund_insufficient", payment_id=payment_id,
                    user_id=user_id, amount=amount,
                )
                return INSUFFICIENT
        else:
            # plan intent: revoke the plan if the user still holds it; no money moves
            user = await self.session.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
            if user is not None and user.plan == plan_key:
                user.plan = "free"
                user.plan_expires_at = None
                await self.session.execute(
                    Subscription.__table__.update()
                    .where(
                        Subscription.user_id == user_id,
                        Subscription.is_active.is_(True),
                    )
                    .values(is_active=False)
                )

        payment.status = "refunded"
        payment.refund_reason = (reason or "").strip()[:255] or None
        payment.refunded_by = panel_user_id
        payment.refunded_at = datetime.now(timezone.utc)
        await self.session.commit()  # reversal + status, atomically
        log.info(
            "payment_refunded",
            payment_id=payment_id, user_id=user_id,
            amount=amount, plan=plan_key, by=panel_user_id,
        )

        # best-effort credit note — never breaks the (already committed) refund
        from app.services.invoice_service import safe_record

        await safe_record(
            self.session, user_id=user_id, kind="refund",
            amount=amount, method=method or "card",
            source_ref=f"refund:payment:{payment_id}",
            provider_ref=provider_ref,
        )
        return REFUNDED
