"""Money-safety integration tests (REAL Postgres, TEST_DATABASE_URL).

Proves the wallet/payment/subscription invariants — including under concurrency,
which is where SELECT ... FOR UPDATE actually matters.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import app.workers.main as worker
from app.models import Payment, Plan, Subscription, User, WalletTransaction
from app.services.payment_service import PaymentService
from app.services.subscription_service import PurchaseStatus, SubscriptionService
from app.services.wallet_service import InsufficientFunds, WalletService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


async def _new_user(maker, telegram_id: int) -> int:
    async with maker() as s:
        user = User(telegram_id=telegram_id)
        s.add(user)
        await s.commit()
        return user.id


async def _ledger(maker, uid: int):
    async with maker() as s:
        rows = (
            await s.scalars(
                select(WalletTransaction)
                .where(WalletTransaction.user_id == uid)
                .order_by(WalletTransaction.id)
            )
        ).all()
        bal = await WalletService(s).balance(uid)
    return rows, bal


def _assert_invariant(rows, balance):
    running = 0
    for r in rows:
        running += r.amount
        assert r.balance_after == running, "balance_after must equal running balance"
        assert r.balance_after >= 0, "balance must never be negative"
    assert sum(r.amount for r in rows) == balance, "SUM(ledger) must equal balance"


# 2.1 -----------------------------------------------------------------------
async def test_ledger_invariant(pg_sessionmaker):
    uid = await _new_user(pg_sessionmaker, 101)
    plan = [("credit", 1000, "deposit"), ("debit", 300, "purchase"),
            ("credit", 250, "refund"), ("debit", 700, "adjustment")]
    for kind, amount, ttype in plan:
        async with pg_sessionmaker() as s:
            w = WalletService(s)
            await (w.credit if kind == "credit" else w.debit)(uid, amount, ttype=ttype)
    rows, bal = await _ledger(pg_sessionmaker, uid)
    assert bal == 250
    _assert_invariant(rows, bal)


# 2.2 -----------------------------------------------------------------------
async def test_insufficient_is_atomic(pg_sessionmaker):
    uid = await _new_user(pg_sessionmaker, 102)
    async with pg_sessionmaker() as s:
        await WalletService(s).credit(uid, 100)
    async with pg_sessionmaker() as s:
        with pytest.raises(InsufficientFunds):
            await WalletService(s).debit(uid, 500)
    rows, bal = await _ledger(pg_sessionmaker, uid)
    assert bal == 100
    assert len(rows) == 1  # no orphan row from the failed debit
    _assert_invariant(rows, bal)


# 2.3 -----------------------------------------------------------------------
async def test_concurrent_wallet_serializes(pg_sessionmaker):
    uid = await _new_user(pg_sessionmaker, 103)
    async with pg_sessionmaker() as s:
        await WalletService(s).credit(uid, 10_000, ttype="deposit")

    async def op(delta: int):
        async with pg_sessionmaker() as s:
            w = WalletService(s)
            if delta > 0:
                await w.credit(uid, delta)
            else:
                await w.debit(uid, -delta)

    tasks = [op(100) for _ in range(50)] + [op(-100) for _ in range(50)]
    await asyncio.gather(*tasks)

    rows, bal = await _ledger(pg_sessionmaker, uid)
    assert bal == 10_000, f"expected 10000, got {bal}"
    assert len(rows) == 101  # seed + 100 ops, none lost
    _assert_invariant(rows, bal)


# 2.4 -----------------------------------------------------------------------
async def test_approve_idempotent_sequential(pg_sessionmaker):
    uid = await _new_user(pg_sessionmaker, 104)
    async with pg_sessionmaker() as s:
        p = Payment(user_id=uid, amount=500, method="card", status="pending")
        s.add(p)
        await s.commit()
        pid = p.id
    async with pg_sessionmaker() as s:
        r1, _ = await PaymentService(s).approve(pid, 999)
    async with pg_sessionmaker() as s:
        r2, _ = await PaymentService(s).approve(pid, 999)
    assert r1 == "approved" and r2 == "already"

    async with pg_sessionmaker() as s:
        bal = await WalletService(s).balance(uid)
        deps = (
            await s.scalars(
                select(WalletTransaction).where(
                    WalletTransaction.user_id == uid,
                    WalletTransaction.type == "deposit",
                )
            )
        ).all()
        pay = await s.scalar(select(Payment).where(Payment.id == pid))
    assert bal == 500
    assert len(deps) == 1 and deps[0].reference == f"payment:{pid}"
    assert pay.status == "approved"


# 2.5 (the critical race) ---------------------------------------------------
async def test_approve_idempotent_concurrent(pg_sessionmaker):
    uid = await _new_user(pg_sessionmaker, 105)
    async with pg_sessionmaker() as s:
        p = Payment(user_id=uid, amount=800, method="card", status="pending")
        s.add(p)
        await s.commit()
        pid = p.id

    async def do():
        async with pg_sessionmaker() as s:
            return await PaymentService(s).approve(pid, 999)

    results = await asyncio.gather(do(), do(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"approval raised under concurrency: {errors}"

    async with pg_sessionmaker() as s:
        bal = await WalletService(s).balance(uid)
        deps = int(
            await s.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.user_id == uid,
                    WalletTransaction.type == "deposit",
                )
            )
        )
    assert bal == 800, f"double-credit! balance={bal}"
    assert deps == 1, f"expected exactly one deposit, got {deps}"


# 2.6 -----------------------------------------------------------------------
async def test_reject_credits_nothing(pg_sessionmaker):
    uid = await _new_user(pg_sessionmaker, 106)
    async with pg_sessionmaker() as s:
        p = Payment(user_id=uid, amount=400, method="card", status="pending")
        s.add(p)
        await s.commit()
        pid = p.id
    async with pg_sessionmaker() as s:
        await PaymentService(s).reject(pid, 999)
    async with pg_sessionmaker() as s:
        bal = await WalletService(s).balance(uid)
        cnt = int(
            await s.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.user_id == uid
                )
            )
        )
        pay = await s.scalar(select(Payment).where(Payment.id == pid))
    assert bal == 0 and cnt == 0
    assert pay.status == "rejected"


# 2.7 -----------------------------------------------------------------------
async def test_subscription_purchase_consistency(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        s.add(Plan(key="plus", title="Plus", price=1000, duration_days=30, max_files=100))
        user = User(telegram_id=107, plan="free", balance=0)
        s.add(user)
        await s.commit()
        uid = user.id

    # insufficient -> nothing changes
    async with pg_sessionmaker() as s:
        u = await s.get(User, uid)
        result = await SubscriptionService(s).purchase(u, "plus")
        assert result.status is PurchaseStatus.INSUFFICIENT
    async with pg_sessionmaker() as s:
        u = await s.get(User, uid)
        subs = int(await s.scalar(select(func.count(Subscription.id)).where(Subscription.user_id == uid)))
        assert u.plan == "free" and u.plan_expires_at is None and subs == 0
        assert await WalletService(s).balance(uid) == 0

    # sufficient -> debits once, sets plan + expiry, one subscription
    async with pg_sessionmaker() as s:
        await WalletService(s).credit(uid, 2000)
    async with pg_sessionmaker() as s:
        u = await s.get(User, uid)
        result = await SubscriptionService(s).purchase(u, "plus")
        assert result.status is PurchaseStatus.OK
    async with pg_sessionmaker() as s:
        u = await s.get(User, uid)
        subs = int(await s.scalar(select(func.count(Subscription.id)).where(Subscription.user_id == uid)))
        purchases = int(await s.scalar(select(func.count(WalletTransaction.id)).where(
            WalletTransaction.user_id == uid, WalletTransaction.type == "purchase")))
        bal = await WalletService(s).balance(uid)
    assert u.plan == "plus" and u.plan_expires_at is not None
    assert subs == 1 and purchases == 1 and bal == 1000


# 2.8 -----------------------------------------------------------------------
async def test_expiry_sweep_downgrades(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        user = User(
            telegram_id=108, plan="plus",
            plan_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        s.add(user)
        await s.commit()
        uid = user.id
        s.add(Subscription(user_id=uid, plan="plus", is_active=True))
        await s.commit()

    n = await worker.process_expiry_sweep(pg_sessionmaker)
    assert n >= 1

    async with pg_sessionmaker() as s:
        u = await s.get(User, uid)
        active = int(await s.scalar(select(func.count(Subscription.id)).where(
            Subscription.user_id == uid, Subscription.is_active.is_(True))))
    assert u.plan == "free"
    assert active == 0
