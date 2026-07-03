"""Reconcile stuck gateway payments (L1) — batch re-verify + expiry.

Runs the EXISTING idempotent ``verify_order`` (payment row FOR UPDATE +
status check inside) over this tenant's ``pending`` gateway payments, so a
truly-paid order settles exactly once and nothing ever double-credits. A row
that is still unpaid after ``EXPIRE_AFTER_HOURS`` is marked ``expired``.

``expired`` is deliberately NOT terminal for verification: ``verify_and_apply``
only short-circuits approved/rejected, so a later manual «بازبینی درگاه» on an
expired order can still settle it (recovery path for late gateway callbacks).

Card (manual) and Telegram-Stars rows are never touched — they have no
gateway to re-query.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.payment import Payment

log = get_logger("reconcile")

EXPIRE_AFTER_HOURS = 24  # documented: unpaid gateway orders older than this expire
BATCH_LIMIT = 200  # oldest first; run again for more

_SKIP_METHODS = ("card", "telegram_stars")


def _is_stale(created_at: datetime | None, now: datetime) -> bool:
    if created_at is None:
        return False
    if created_at.tzinfo is None:  # SQLite returns naive timestamps
        created_at = created_at.replace(tzinfo=timezone.utc)
    return now - created_at > timedelta(hours=EXPIRE_AFTER_HOURS)


async def reconcile_pending(session: AsyncSession, *, verify=None) -> dict[str, int]:
    """Returns the report: settled / already / mismatch / expired / pending.

    ``verify`` is injectable for tests; production uses the real
    ``providers.verify_order`` (idempotent, provider-dispatched).
    """
    if verify is None:
        from app.services.providers import verify_order as verify

    rows = list(
        await session.scalars(
            select(Payment)
            .where(
                Payment.status == "pending",
                Payment.method.notin_(_SKIP_METHODS),
            )
            .order_by(Payment.id)
            .limit(BATCH_LIMIT)
        )
    )
    now = datetime.now(timezone.utc)
    report = {"settled": 0, "already": 0, "mismatch": 0, "expired": 0, "pending": 0}
    for row in rows:
        payment_id, created_at = row.id, row.created_at
        result = await verify(session, payment_id)
        if result == "credited":
            report["settled"] += 1
        elif result == "already":
            report["already"] += 1
        elif result == "mismatch":
            report["mismatch"] += 1
        else:  # "failed" — gateway says unpaid (or transient error): no credit
            if _is_stale(created_at, now):
                # re-load under lock: verify may have raced a real settlement
                fresh = await session.scalar(
                    select(Payment)
                    .where(Payment.id == payment_id, Payment.status == "pending")
                    .with_for_update()
                )
                if fresh is not None:
                    fresh.status = "expired"
                    await session.commit()
                    report["expired"] += 1
                    continue
            report["pending"] += 1
    log.info("reconcile_done", **report)
    return report
