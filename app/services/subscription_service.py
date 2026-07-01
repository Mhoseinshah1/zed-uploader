"""SubscriptionService — buy/extend plans, consistent with the wallet debit."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.plans import plan_rank
from app.models.subscription import Subscription
from app.models.user import User
from app.services.plan_service import PlanService
from app.services.wallet_service import InsufficientFunds, WalletService

log = get_logger("subscription")


class PurchaseStatus(str, Enum):
    OK = "ok"
    NOT_AVAILABLE = "not_available"
    INSUFFICIENT = "insufficient"


@dataclass
class PurchaseResult:
    status: PurchaseStatus
    expires_at: datetime | None = None
    price: int = 0


class SubscriptionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def purchase(self, user: User, plan_key: str) -> PurchaseResult:
        plan = await PlanService(self.session).get(plan_key)
        if plan is None or not plan.is_active:
            return PurchaseResult(PurchaseStatus.NOT_AVAILABLE)

        # Charge first; only mutate the plan after a successful debit.
        if plan.price > 0:
            try:
                await WalletService(self.session).debit(
                    user.id,
                    plan.price,
                    ttype="purchase",
                    reference=f"plan:{plan_key}",
                    description=f"خرید پلن {plan.title}",
                )
            except InsufficientFunds:
                return PurchaseResult(PurchaseStatus.INSUFFICIENT, price=plan.price)

        now = datetime.now(timezone.utc)
        expires: datetime | None
        if plan.duration_days == 0:
            expires = None
        else:
            base = now
            current_exp = user.plan_expires_at
            if current_exp is not None and current_exp.tzinfo is None:
                current_exp = current_exp.replace(tzinfo=timezone.utc)
            # extend from the current expiry only if same-or-higher & unexpired
            if (
                plan_rank(user.plan) >= plan_rank(plan_key)
                and current_exp is not None
                and current_exp > now
            ):
                base = current_exp
            expires = base + timedelta(days=plan.duration_days)

        user.plan = plan_key
        user.plan_expires_at = expires
        self.session.add(
            Subscription(
                user_id=user.id, plan=plan_key, starts_at=now, expires_at=expires,
                is_active=True,
            )
        )
        await self.session.commit()
        log.info("plan_purchased", user_id=user.id, plan=plan_key, price=plan.price)
        return PurchaseResult(PurchaseStatus.OK, expires_at=expires, price=plan.price)
