"""C4 integration (REAL Postgres): Stars idempotency under concurrency.

The partial unique index on payments(provider_ref) WHERE method='telegram_stars'
must hold even when two identical successful_payment updates race.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.models import Payment, Plan, Subscription, User, WalletTransaction
from app.services.stars_service import ACTIVATED, ALREADY, StarsService
from app.services.wallet_service import WalletService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


async def test_concurrent_duplicate_charge_activates_once(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        s.add(Plan(key="plus", title="Plus", price=3000, duration_days=30,
                   max_files=100, stars_price=75))
        user = User(telegram_id=7501)
        s.add(user)
        await s.commit()
        uid = user.id

    async def do():
        async with pg_sessionmaker() as s:
            u = await s.get(User, uid)
            return await StarsService(s).apply_successful_payment(
                u, "plan:plus", "CHG-RACE", 75, "XTR"
            )

    results = await asyncio.gather(do(), do(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"raised under concurrency: {errors}"
    assert sorted(results) == sorted([ACTIVATED, ALREADY])

    async with pg_sessionmaker() as s:
        user = await s.get(User, uid)
        payments = int(await s.scalar(select(func.count(Payment.id)).where(
            Payment.user_id == uid, Payment.method == "telegram_stars")))
        deposits = int(await s.scalar(select(func.count(WalletTransaction.id)).where(
            WalletTransaction.user_id == uid, WalletTransaction.type == "deposit")))
        purchases = int(await s.scalar(select(func.count(WalletTransaction.id)).where(
            WalletTransaction.user_id == uid, WalletTransaction.type == "purchase")))
        subs = int(await s.scalar(select(func.count(Subscription.id)).where(
            Subscription.user_id == uid)))
        rows = (await s.scalars(select(WalletTransaction).where(
            WalletTransaction.user_id == uid))).all()
        bal = await WalletService(s).balance(uid)

    assert user.plan == "plus"
    assert (payments, deposits, purchases, subs) == (1, 1, 1, 1)
    assert bal == 0 and sum(r.amount for r in rows) == bal  # ledger invariant
