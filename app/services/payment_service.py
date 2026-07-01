"""PaymentService — top-up requests and idempotent approval.

Approval re-loads the payment FOR UPDATE and no-ops if already approved, so it
never double-credits. Crediting goes through WalletService (the only balance
writer) in the same transaction.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.payment import Payment
from app.services.wallet_service import WalletService

log = get_logger("payment")


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, user_id: int, amount: int, method: str, receipt: str | None
    ) -> Payment:
        payment = Payment(
            user_id=user_id, amount=amount, method=method, receipt=receipt,
            status="pending",
        )
        self.session.add(payment)
        await self.session.commit()
        return payment

    async def get(self, payment_id: int) -> Payment | None:
        return await self.session.scalar(
            select(Payment).where(Payment.id == payment_id)
        )

    async def approve(
        self, payment_id: int, admin_telegram_id: int
    ) -> tuple[str, Payment | None]:
        """Idempotent: returns ('approved'|'already'|'not_found', payment)."""
        payment = await self.session.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
        if payment is None:
            return "not_found", None
        if payment.status == "approved":
            return "already", payment
        payment.status = "approved"
        payment.admin_id = admin_telegram_id
        # credit() commits, persisting the status change atomically with the ledger row
        await WalletService(self.session).credit(
            payment.user_id,
            payment.amount,
            ttype="deposit",
            reference=f"payment:{payment.id}",
            description="شارژ کیف پول",
        )
        log.info("payment_approved", payment_id=payment.id, user_id=payment.user_id)
        return "approved", payment

    async def reject(
        self, payment_id: int, admin_telegram_id: int
    ) -> Payment | None:
        payment = await self.session.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
        if payment is None:
            return None
        if payment.status == "pending":
            payment.status = "rejected"
            payment.admin_id = admin_telegram_id
            await self.session.commit()
            log.info("payment_rejected", payment_id=payment.id)
        return payment
