"""Telegram Stars (XTR) plan purchases.

Mirrors the gateways' plan-intent flow so every guarantee is reused: a Stars
charge credits the wallet with the plan's Toman price (deposit, through
WalletService — the ledger invariant holds) and then activates the plan via
the EXISTING atomic SubscriptionService.purchase. The Telegram
``telegram_payment_charge_id`` is the idempotency key: checked up front AND
enforced by a partial unique index on payments, so a duplicate (or concurrent
duplicate) successful_payment can never activate twice.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.payment import Payment
from app.models.plan import Plan
from app.models.user import User
from app.services.subscription_service import PurchaseStatus, SubscriptionService
from app.services.wallet_service import WalletService

log = get_logger("stars")

METHOD = "telegram_stars"

# apply outcomes
ACTIVATED = "activated"
ALREADY = "already"
INVALID = "invalid"
FAILED = "failed"


class StarsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _plan(self, plan_key: str) -> Plan | None:
        return await self.session.scalar(select(Plan).where(Plan.key == plan_key))

    async def validate_pre_checkout(
        self, payload: str, total_amount: int, currency: str
    ) -> str | None:
        """None when the checkout may proceed, else a Persian error message."""
        from app.bot import messages

        if currency != "XTR" or not payload.startswith("plan:"):
            return messages.STARS_INVALID
        plan = await self._plan(payload.split(":", 1)[1])
        if (
            plan is None
            or not plan.is_active
            or plan.stars_price is None
            or int(total_amount) != int(plan.stars_price)
        ):
            return messages.STARS_INVALID
        return None

    async def apply_successful_payment(
        self, user: User, payload: str, charge_id: str, total_amount: int, currency: str
    ) -> str:
        """Idempotently record a Stars charge and activate the plan."""
        if not charge_id:
            return INVALID
        error = await self.validate_pre_checkout(payload, total_amount, currency)
        if error is not None:
            log.error(
                "stars_payment_invalid",
                user_id=user.id, payload=payload, amount=total_amount,
            )
            return INVALID
        plan_key = payload.split(":", 1)[1]
        plan = await self._plan(plan_key)

        # idempotency: one payment row per charge id (fast path + DB index)
        existing = await self.session.scalar(
            select(Payment.id).where(
                Payment.method == METHOD, Payment.provider_ref == charge_id
            )
        )
        if existing is not None:
            return ALREADY

        payment = Payment(
            user_id=user.id,
            amount=plan.price,  # ledger accounts in Toman
            method=METHOD,
            provider=METHOD,
            status="approved",
            provider_ref=charge_id,
            intent=f"plan:{plan_key}",
        )
        self.session.add(payment)
        try:
            if plan.price > 0:
                # WalletService.credit commits — the payment row rides along
                await WalletService(self.session).credit(
                    user.id,
                    plan.price,
                    ttype="deposit",
                    reference=f"{METHOD}:{charge_id}",
                    description="Telegram Stars",
                )
            else:
                await self.session.commit()
        except IntegrityError:  # concurrent duplicate hit the unique index
            await self.session.rollback()
            return ALREADY

        result = await SubscriptionService(self.session).purchase(user, plan_key)
        if result.status is not PurchaseStatus.OK:
            # charge recorded + wallet credited; the user can buy from wallet
            log.error(
                "stars_activation_failed",
                user_id=user.id, plan=plan_key, status=result.status,
            )
            return FAILED
        log.info(
            "stars_plan_activated", user_id=user.id, plan=plan_key, charge=charge_id
        )
        return ACTIVATED
