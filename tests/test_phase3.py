"""Phase 3 tests — wallet ledger, idempotent approval, feature gating, purchase.

In-memory SQLite (aiosqlite); no live DB/Redis/network.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, FeatureFlag, Payment, Plan, User, WalletTransaction
from app.services.feature_service import FeatureService
from app.services.payment_service import PaymentService
from app.services.subscription_service import PurchaseStatus, SubscriptionService
from app.services.wallet_service import InsufficientFunds, WalletService


async def _setup():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


# --------------------------------------------------------------------------
# ledger invariant
# --------------------------------------------------------------------------
async def _ledger_invariant() -> None:
    engine, Session = await _setup()
    async with Session() as s:
        user = User(telegram_id=1)
        s.add(user)
        await s.commit()
        w = WalletService(s)
        await w.credit(user.id, 1000)
        await w.debit(user.id, 300)
        await w.credit(user.id, 200, ttype="refund")

        balance = await w.balance(user.id)
        ledger_sum = int(
            await s.scalar(
                select(func.sum(WalletTransaction.amount)).where(
                    WalletTransaction.user_id == user.id
                )
            )
            or 0
        )
        assert balance == 900
        assert ledger_sum == balance
    await engine.dispose()


def test_ledger_invariant():
    asyncio.run(_ledger_invariant())


# --------------------------------------------------------------------------
# debit guard
# --------------------------------------------------------------------------
async def _insufficient() -> None:
    engine, Session = await _setup()
    async with Session() as s:
        user = User(telegram_id=2)
        s.add(user)
        await s.commit()
        w = WalletService(s)
        await w.credit(user.id, 100)

        raised = False
        try:
            await w.debit(user.id, 500)
        except InsufficientFunds:
            raised = True
        assert raised

        assert await w.balance(user.id) == 100
        count = int(
            await s.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.user_id == user.id
                )
            )
        )
        assert count == 1  # only the initial credit; failed debit added no row
    await engine.dispose()


def test_debit_insufficient_no_change():
    asyncio.run(_insufficient())


# --------------------------------------------------------------------------
# idempotent payment approval
# --------------------------------------------------------------------------
async def _approve_twice() -> None:
    engine, Session = await _setup()
    async with Session() as s:
        user = User(telegram_id=3)
        s.add(user)
        await s.commit()
        payment = Payment(user_id=user.id, amount=500, method="card", status="pending")
        s.add(payment)
        await s.commit()

        svc = PaymentService(s)
        status1, _ = await svc.approve(payment.id, admin_telegram_id=999)
        status2, _ = await svc.approve(payment.id, admin_telegram_id=999)
        assert status1 == "approved"
        assert status2 == "already"

        assert await WalletService(s).balance(user.id) == 500
        deposits = int(
            await s.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.user_id == user.id,
                    WalletTransaction.type == "deposit",
                )
            )
        )
        assert deposits == 1  # credited exactly once
    await engine.dispose()


def test_payment_approval_idempotent():
    asyncio.run(_approve_twice())


# --------------------------------------------------------------------------
# feature gating
# --------------------------------------------------------------------------
async def _feature_gating() -> None:
    engine, Session = await _setup()
    async with Session() as s:
        s.add(FeatureFlag(key="protect_content", is_enabled=True, plan="plus"))
        free_user = User(telegram_id=10, plan="free")
        plus_user = User(
            telegram_id=11,
            plan="plus",
            plan_expires_at=datetime.now(timezone.utc) + timedelta(days=5),
        )
        s.add_all([free_user, plus_user])
        await s.commit()

        assert await FeatureService.is_enabled(s, "protect_content", free_user) is False
        assert await FeatureService.is_enabled(s, "protect_content", plus_user) is True
    await engine.dispose()


def test_feature_gating():
    asyncio.run(_feature_gating())


# --------------------------------------------------------------------------
# purchase with insufficient funds does not change plan
# --------------------------------------------------------------------------
async def _purchase_insufficient() -> None:
    engine, Session = await _setup()
    async with Session() as s:
        s.add(Plan(key="plus", title="Plus", price=1000, duration_days=30, max_files=100))
        user = User(telegram_id=20, plan="free", balance=0)
        s.add(user)
        await s.commit()

        result = await SubscriptionService(s).purchase(user, "plus")
        assert result.status is PurchaseStatus.INSUFFICIENT
        assert user.plan == "free"
        assert user.plan_expires_at is None
        assert await WalletService(s).balance(user.id) == 0
    await engine.dispose()


def test_purchase_insufficient_keeps_plan():
    asyncio.run(_purchase_insufficient())


# --------------------------------------------------------------------------
# purchase success sets plan + expiry and debits
# --------------------------------------------------------------------------
async def _purchase_ok() -> None:
    engine, Session = await _setup()
    async with Session() as s:
        s.add(Plan(key="plus", title="Plus", price=1000, duration_days=30, max_files=100))
        user = User(telegram_id=21, plan="free", balance=0)
        s.add(user)
        await s.commit()
        await WalletService(s).credit(user.id, 2000)

        result = await SubscriptionService(s).purchase(user, "plus")
        assert result.status is PurchaseStatus.OK
        assert user.plan == "plus"
        assert user.plan_expires_at is not None
        assert await WalletService(s).balance(user.id) == 1000
    await engine.dispose()


def test_purchase_success():
    asyncio.run(_purchase_ok())
