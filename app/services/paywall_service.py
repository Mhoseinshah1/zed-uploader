"""Paywall (J6) — plan gates, one-time media purchases, free daily quota.

Rules enforced in ``deliver_by_code`` BEFORE the download claim:
  * ``media.required_plan`` — the user needs that plan or higher;
  * ``media.price`` — the user needs a settled entitlement (a media_purchases
    row, committed atomically with the wallet debit — exactly-once by DB
    constraint) unless they own the file;
  * free daily quota (per-tenant BotSetting, 0 = off) — free-plan users get at
    most N deliveries per day, counted atomically in Redis.
Tenant admins / env owners bypass all three (they manage the content).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.plans import plan_rank
from app.core.redis_client import get_redis
from app.core.tenant_context import current_tenant
from app.models.media import Media
from app.models.purchase import MediaPurchase
from app.models.user import User
from app.services.bot_setting_service import KEY_FREE_DAILY_QUOTA, BotSettingService
from app.services.wallet_service import InsufficientFunds, WalletService

log = get_logger("paywall")

# check_access outcomes
OK = "ok"
PLAN_REQUIRED = "plan_required"
PAYMENT_REQUIRED = "payment_required"
QUOTA_EXCEEDED = "quota_exceeded"

# purchase outcomes
PURCHASED = "ok"
ALREADY = "already"
INSUFFICIENT = "insufficient"
NOT_FOR_SALE = "not_for_sale"

BUY_LOCK_TTL = 10  # seconds — double-tap debounce per (media, user)


def _quota_key(user_id: int) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"dlq:{current_tenant()}:{user_id}:{today}"


class PaywallService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def has_entitlement(self, media_id: int, user_id: int) -> bool:
        row = await self.session.scalar(
            select(MediaPurchase.id).where(
                MediaPurchase.media_id == media_id,
                MediaPurchase.user_id == user_id,
            )
        )
        return row is not None

    async def check_access(
        self, media: Media, user: User | None, telegram_id: int
    ) -> str:
        """The paywall gate — evaluated before the download claim."""
        from app.services.admin_service import AdminService

        if await AdminService.is_admin(self.session, telegram_id):
            return OK  # admins/owners manage the content
        if user is None:
            # an anonymous chat can't hold a plan/entitlement — only fully
            # free media pass
            if media.required_plan or (media.price or 0) > 0:
                return PAYMENT_REQUIRED if (media.price or 0) > 0 else PLAN_REQUIRED
            return OK

        if media.required_plan and plan_rank(user.effective_plan) < plan_rank(
            media.required_plan
        ):
            return PLAN_REQUIRED

        if (media.price or 0) > 0:
            if media.owner_user_id != user.id and not await self.has_entitlement(
                media.id, user.id
            ):
                return PAYMENT_REQUIRED

        # free daily quota (free-plan users only; 0/unset = off)
        quota = await BotSettingService(self.session).get_int(KEY_FREE_DAILY_QUOTA, 0)
        if quota > 0 and user.effective_plan == "free":
            try:
                used = int(await get_redis().get(_quota_key(user.id)) or 0)
            except Exception:
                used = 0  # Redis down -> fail open on the SOFT quota only
            if used >= quota:
                return QUOTA_EXCEEDED
        return OK

    async def count_delivery(self, user: User | None) -> None:
        """Atomically count a successful delivery against the free daily quota."""
        if user is None or user.effective_plan != "free":
            return
        quota = await BotSettingService(self.session).get_int(KEY_FREE_DAILY_QUOTA, 0)
        if quota <= 0:
            return
        try:
            redis = get_redis()
            key = _quota_key(user.id)
            n = await redis.incr(key)  # atomic
            if n == 1:
                await redis.expire(key, 26 * 60 * 60)  # today + slack
        except Exception:  # the counter is best-effort; delivery already done
            pass

    async def purchase(self, media: Media, user: User) -> str:
        """Buy a paid media from the wallet — atomic + exactly-once.

        The debit (WalletService.debit_nocommit) and the entitlement row commit
        together; a duplicate/concurrent buy folds into the unique constraint.
        A best-effort invoice records the settled charge post-commit.
        """
        price = int(media.price or 0)
        if price <= 0:
            return NOT_FOR_SALE
        if await self.has_entitlement(media.id, user.id):
            return ALREADY

        redis = get_redis()
        lock_key = f"mediabuy:lock:{media.id}:{user.id}"
        try:
            acquired = await redis.set(lock_key, "1", nx=True, ex=BUY_LOCK_TTL)
        except Exception:
            acquired = True  # fail-open: Redis down must not block purchases
        if not acquired:
            return ALREADY

        release_lock = True
        try:
            try:
                await WalletService(self.session).debit_nocommit(
                    user.id, price, ttype="purchase",
                    reference=f"media:{media.id}:user:{user.id}",
                    description=f"خرید فایل {media.code}",
                )
            except InsufficientFunds:
                await self.session.rollback()
                return INSUFFICIENT
            self.session.add(
                MediaPurchase(media_id=media.id, user_id=user.id, amount=price)
            )
            await self.session.commit()  # debit + entitlement, atomically
            release_lock = False  # success: hold the lock as a debounce window
        except IntegrityError:  # concurrent duplicate hit the unique constraint
            await self.session.rollback()
            return ALREADY
        except Exception as exc:
            await self.session.rollback()
            log.error("media_purchase_failed", media_id=media.id, error=str(exc))
            return INSUFFICIENT
        finally:
            if release_lock:
                try:
                    await redis.delete(lock_key)
                except Exception:
                    pass

        # record layer only — never breaks the settled purchase
        from app.services.invoice_service import safe_record

        await safe_record(
            self.session, user_id=user.id, kind="media", amount=price,
            method="wallet", source_ref=f"media:{media.id}:user:{user.id}",
        )
        log.info("media_purchased", media_id=media.id, user_id=user.id, price=price)
        return PURCHASED
