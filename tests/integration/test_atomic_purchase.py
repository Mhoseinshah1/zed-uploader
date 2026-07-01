"""A1 — atomic plan purchase (REAL Postgres): all-or-nothing + double-tap dedup."""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.models import Plan, Subscription, User, WalletTransaction
from app.services.subscription_service import PurchaseStatus, SubscriptionService
from app.services.wallet_service import WalletService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


async def _seed(maker, telegram_id, balance, price=1000):
    async with maker() as s:
        s.add(Plan(key="plus", title="Plus", price=price, duration_days=30, max_files=100))
        user = User(telegram_id=telegram_id, plan="free")
        s.add(user)
        await s.commit()
        uid = user.id
        if balance:
            await WalletService(s).credit(uid, balance)
    return uid


async def _counts(maker, uid):
    async with maker() as s:
        bal = await WalletService(s).balance(uid)
        purchases = int(await s.scalar(select(func.count(WalletTransaction.id)).where(
            WalletTransaction.user_id == uid, WalletTransaction.type == "purchase")))
        subs = int(await s.scalar(select(func.count(Subscription.id)).where(
            Subscription.user_id == uid)))
        user = await s.get(User, uid)
        rows = (await s.scalars(select(WalletTransaction).where(
            WalletTransaction.user_id == uid))).all()
    return bal, purchases, subs, user.plan, rows


def _assert_invariant(rows, bal):
    assert sum(r.amount for r in rows) == bal


# success is all-or-nothing ------------------------------------------------
async def test_purchase_atomic_success(pg_sessionmaker):
    uid = await _seed(pg_sessionmaker, 301, balance=2000, price=1000)
    async with pg_sessionmaker() as s:
        user = await s.get(User, uid)
        result = await SubscriptionService(s).purchase(user, "plus")
    assert result.status is PurchaseStatus.OK

    bal, purchases, subs, plan, rows = await _counts(pg_sessionmaker, uid)
    assert bal == 1000 and purchases == 1 and subs == 1 and plan == "plus"
    _assert_invariant(rows, bal)


# failure after debit -> full rollback (nothing charged, no plan/sub) -------
async def test_purchase_rolls_back_on_failure(pg_sessionmaker, monkeypatch):
    uid = await _seed(pg_sessionmaker, 302, balance=2000, price=1000)

    async with pg_sessionmaker() as s:
        user = await s.get(User, uid)

        real_commit = s.commit
        calls = {"n": 0}

        async def boom():
            calls["n"] += 1
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(s, "commit", boom)
        result = await SubscriptionService(s).purchase(user, "plus")
        monkeypatch.setattr(s, "commit", real_commit)
    assert result.status is PurchaseStatus.FAILED
    assert calls["n"] == 1  # the single purchase commit was attempted

    bal, purchases, subs, plan, rows = await _counts(pg_sessionmaker, uid)
    assert bal == 2000, "debit must be rolled back"
    assert purchases == 0 and subs == 0 and plan == "free"
    _assert_invariant(rows, bal)


# insufficient funds -> no debit, no plan ----------------------------------
async def test_purchase_insufficient(pg_sessionmaker):
    uid = await _seed(pg_sessionmaker, 303, balance=0, price=1000)
    async with pg_sessionmaker() as s:
        user = await s.get(User, uid)
        result = await SubscriptionService(s).purchase(user, "plus")
    assert result.status is PurchaseStatus.INSUFFICIENT

    bal, purchases, subs, plan, rows = await _counts(pg_sessionmaker, uid)
    assert bal == 0 and purchases == 0 and subs == 0 and plan == "free"


# concurrent double-tap purchases exactly once -----------------------------
async def test_purchase_double_tap_once(pg_sessionmaker):
    uid = await _seed(pg_sessionmaker, 304, balance=5000, price=1000)

    async def do():
        async with pg_sessionmaker() as s:
            user = await s.get(User, uid)
            return (await SubscriptionService(s).purchase(user, "plus")).status

    results = await asyncio.gather(do(), do(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"raised under concurrency: {errors}"

    bal, purchases, subs, plan, rows = await _counts(pg_sessionmaker, uid)
    assert purchases == 1, f"charged {purchases} times (double purchase!)"
    assert subs == 1 and bal == 4000 and plan == "plus"
    assert PurchaseStatus.OK in results and PurchaseStatus.DUPLICATE in results
    _assert_invariant(rows, bal)
