"""SubscriptionService — atomic plan purchase (single transaction) + double-tap
dedup.

The wallet debit, the ``user.plan``/``plan_expires_at`` update, and the
``subscriptions`` insert all happen in ONE transaction and commit together, so a
crash can never debit without granting the plan (or vice versa). A short Redis
lock per (user, plan) dedups a double-tap. The public ``WalletService.credit``/
``debit`` behavior is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.plans import plan_rank
from app.core.redis_client import get_redis
from app.models.subscription import Subscription
from app.models.user import User
from app.services.plan_service import PlanService
from app.services.wallet_service import InsufficientFunds, WalletService

log = get_logger("subscription")

PURCHASE_LOCK_TTL = 10  # seconds — double-tap debounce window per (user, plan)


class PurchaseStatus(str, Enum):
    OK = "ok"
    NOT_AVAILABLE = "not_available"
    INSUFFICIENT = "insufficient"
    DUPLICATE = "duplicate"  # a concurrent purchase for the same (user, plan)
    FAILED = "failed"        # unexpected error — rolled back, nothing charged


@dataclass
class PurchaseResult:
    status: PurchaseStatus
    expires_at: datetime | None = None
    price: int = 0
    invoice_no: int | None = None  # H4: the receipt number, when one was issued


class SubscriptionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _compute_expiry(self, user: User, plan_key: str, duration_days: int, now):
        if duration_days == 0:
            return None
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
        return base + timedelta(days=duration_days)

    async def purchase(
        self, user: User, plan_key: str, method: str = "wallet"
    ) -> PurchaseResult:
        plan = await PlanService(self.session).get(plan_key)
        if plan is None or not plan.is_active:
            return PurchaseResult(PurchaseStatus.NOT_AVAILABLE)

        # Snapshot plan fields into locals up front: after a rollback the ORM
        # instance is expired and touching ``plan.price``/``plan.title`` would
        # emit a lazy refresh outside the async greenlet (MissingGreenlet).
        user_id = user.id
        price = plan.price
        title = plan.title
        duration_days = plan.duration_days

        # --- double-tap dedup: a per-(user, plan) lock. On a successful purchase
        # the lock is intentionally NOT released; it lingers for its short TTL so
        # a second tap arriving right after the first commit still folds into a
        # single charge. It is released immediately when nothing was charged
        # (insufficient / error) so an honest retry is not blocked. ------------
        redis = get_redis()
        lock_key = f"purchase:lock:{user_id}:{plan_key}"
        try:
            acquired = await redis.set(lock_key, "1", nx=True, ex=PURCHASE_LOCK_TTL)
        except Exception:
            acquired = True  # fail-open: Redis down must not block purchases
        if not acquired:
            return PurchaseResult(PurchaseStatus.DUPLICATE)

        release_lock = True  # keep the lock only on a committed purchase
        try:
            # single transaction: debit (no commit) + plan + subscription, one commit
            if price > 0:
                try:
                    await WalletService(self.session).debit_nocommit(
                        user_id,
                        price,
                        ttype="purchase",
                        reference=f"plan:{plan_key}",
                        description=f"خرید پلن {title}",
                    )
                except InsufficientFunds:
                    await self.session.rollback()
                    return PurchaseResult(PurchaseStatus.INSUFFICIENT, price=price)

            now = datetime.now(timezone.utc)
            expires = self._compute_expiry(user, plan_key, duration_days, now)
            user.plan = plan_key
            user.plan_expires_at = expires
            subscription = Subscription(
                user_id=user_id, plan=plan_key, starts_at=now,
                expires_at=expires, is_active=True,
            )
            self.session.add(subscription)
            await self.session.commit()  # debit + plan + subscription, atomically
            release_lock = False  # success: hold the lock as a debounce window
            log.info("plan_purchased", user_id=user_id, plan=plan_key, price=price)
            # H4: a paid plan purchase is a settled payment -> one receipt
            # (best-effort, post-commit; keyed to the subscription so it is issued
            # exactly once). Free plans (price 0) are not payments -> no invoice.
            invoice_no = None
            if price > 0:
                from app.services.invoice_service import safe_record

                inv = await safe_record(
                    self.session, user_id=user_id, kind="plan", amount=price,
                    method=method, source_ref=f"sub:{subscription.id}",
                )
                invoice_no = inv.invoice_no if inv is not None else None
            return PurchaseResult(
                PurchaseStatus.OK, expires_at=expires, price=price, invoice_no=invoice_no
            )
        except Exception as exc:  # anything after debit fails -> full rollback
            await self.session.rollback()
            log.error(
                "plan_purchase_failed", user_id=user_id, plan=plan_key, error=str(exc)
            )
            return PurchaseResult(PurchaseStatus.FAILED, price=price)
        finally:
            if release_lock:
                try:
                    await redis.delete(lock_key)
                except Exception:
                    pass
