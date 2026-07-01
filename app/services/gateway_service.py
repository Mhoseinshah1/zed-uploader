"""Generic online-gateway service shared by every PaymentProvider.

This is the CentralPay money-safety core, made provider-agnostic:
  1. idempotent verify keyed on our order (payment row FOR UPDATE + status
     check; an approved order is never re-verified or re-credited — including
     under concurrent double-returns),
  2. an amount (+user when reported) match check before crediting,
  3. credits ONLY through WalletService (ledger invariant),
  4. a "plan:<key>" intent auto-purchases the plan after the deposit.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.payment import Payment
from app.models.user import User
from app.services.providers.base import PaymentProvider
from app.services.subscription_service import SubscriptionService
from app.services.wallet_service import WalletService

log = get_logger("gateway")


class GatewayService:
    def __init__(self, session: AsyncSession, provider: PaymentProvider) -> None:
        self.session = session
        self.provider = provider

    async def start(
        self, user: User, amount: int, intent: str
    ) -> tuple[int, str] | None:
        """Create a pending payment (its id IS our orderId) and get a redirect URL.

        Returns (order_id, redirect_url) on success so the caller can offer a
        "check payment" button, or None if the gateway declined.
        """
        payment = Payment(
            user_id=user.id, amount=amount, method=self.provider.key,
            provider=self.provider.key, status="pending", intent=intent,
        )
        self.session.add(payment)
        await self.session.commit()
        await self.session.refresh(payment)

        redirect_url = await self.provider.create(payment)
        if redirect_url is None:
            log.warning(
                "gateway_start_failed", provider=self.provider.key, order_id=payment.id
            )
            return None
        # persist anything the provider attached (e.g. Zarinpal's Authority)
        await self.session.commit()
        log.info(
            "gateway_started",
            provider=self.provider.key, order_id=payment.id, amount=amount, intent=intent,
        )
        return payment.id, redirect_url

    async def verify_and_apply(self, order_id: int) -> str:
        """Idempotently verify + credit. Returns credited|already|failed|mismatch."""
        payment = await self.session.scalar(
            select(Payment)
            .where(Payment.id == order_id, Payment.method == self.provider.key)
            .with_for_update()
        )
        if payment is None:
            return "failed"
        if payment.status == "approved":
            return "already"  # never re-verify a paid order
        if payment.status == "rejected":
            return "failed"

        result = await self.provider.verify(payment)
        if not result.ok:
            return "failed"  # leave pending; the user may retry

        if (
            result.amount is None
            or int(result.amount) != int(payment.amount)
            or (result.user_id is not None and int(result.user_id) != int(payment.user_id))
        ):
            payment.status = "rejected"
            await self.session.commit()
            log.error(
                "gateway_mismatch",
                provider=self.provider.key, order_id=order_id,
                amount=result.amount, user_id=result.user_id,
            )
            return "mismatch"  # NEVER credit on mismatch

        payment.status = "approved"
        payment.provider_ref = result.ref
        await WalletService(self.session).credit(
            payment.user_id,
            payment.amount,
            ttype="deposit",
            reference=f"{self.provider.key}:{result.ref}",
            description=f"{self.provider.title} deposit",
        )
        await self.session.commit()
        log.info(
            "gateway_credited",
            provider=self.provider.key, order_id=order_id, ref=result.ref,
        )

        # a "plan:<key>" intent auto-runs the purchase after a successful deposit
        if payment.intent and payment.intent.startswith("plan:"):
            user = await self.session.scalar(
                select(User).where(User.id == payment.user_id)
            )
            if user is not None:
                await SubscriptionService(self.session).purchase(
                    user, payment.intent.split(":", 1)[1]
                )
        return "credited"
