"""C4 unit tests — Stars validation, exactly-once activation, handlers.

SQLite + mocked Telegram objects (no network).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base, Payment, Plan, Subscription, User, WalletTransaction
from app.services.stars_service import ACTIVATED, ALREADY, INVALID, StarsService
from app.services.wallet_service import WalletService


@pytest_asyncio.fixture
async def sqlite_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(s, *, stars_price=50, price=10000):
    s.add(Plan(key="plus", title="Plus", price=price, duration_days=30,
               max_files=100, stars_price=stars_price))
    user = User(telegram_id=71)
    s.add(user)
    await s.commit()
    return user


async def _counts(s, uid):
    deposits = int(await s.scalar(select(func.count(WalletTransaction.id)).where(
        WalletTransaction.user_id == uid, WalletTransaction.type == "deposit")))
    purchases = int(await s.scalar(select(func.count(WalletTransaction.id)).where(
        WalletTransaction.user_id == uid, WalletTransaction.type == "purchase")))
    payments = int(await s.scalar(select(func.count(Payment.id)).where(
        Payment.user_id == uid, Payment.method == "telegram_stars")))
    subs = int(await s.scalar(select(func.count(Subscription.id)).where(
        Subscription.user_id == uid)))
    return deposits, purchases, payments, subs


async def test_pre_checkout_validation(sqlite_maker):
    async with sqlite_maker() as s:
        await _seed(s)
        svc = StarsService(s)
        assert await svc.validate_pre_checkout("plan:plus", 50, "XTR") is None
        assert await svc.validate_pre_checkout("plan:plus", 49, "XTR") is not None
        assert await svc.validate_pre_checkout("plan:plus", 50, "USD") is not None
        assert await svc.validate_pre_checkout("junk", 50, "XTR") is not None
        assert await svc.validate_pre_checkout("plan:nope", 50, "XTR") is not None


async def test_successful_payment_activates_exactly_once(sqlite_maker):
    async with sqlite_maker() as s:
        user = await _seed(s)
        svc = StarsService(s)
        r1 = await svc.apply_successful_payment(user, "plan:plus", "CHG-1", 50, "XTR")
        assert r1 == ACTIVATED
        # duplicate charge id -> no double activation
        r2 = await svc.apply_successful_payment(user, "plan:plus", "CHG-1", 50, "XTR")
        assert r2 == ALREADY

        refreshed = await s.get(User, user.id)
        await s.refresh(refreshed)
        assert refreshed.plan == "plus" and refreshed.plan_expires_at is not None
        deposits, purchases, payments, subs = await _counts(s, user.id)
        assert (deposits, purchases, payments, subs) == (1, 1, 1, 1)
        # ledger invariant: deposit +10000, purchase -10000 -> balance 0
        assert await WalletService(s).balance(user.id) == 0
        pay = await s.scalar(select(Payment).where(Payment.provider_ref == "CHG-1"))
        assert pay.status == "approved" and pay.amount == 10000
        assert pay.intent == "plan:plus"


async def test_amount_mismatch_records_nothing(sqlite_maker):
    async with sqlite_maker() as s:
        user = await _seed(s)
        result = await StarsService(s).apply_successful_payment(
            user, "plan:plus", "CHG-2", 49, "XTR"  # wrong stars amount
        )
        assert result == INVALID
        assert await _counts(s, user.id) == (0, 0, 0, 0)
        refreshed = await s.get(User, user.id)
        await s.refresh(refreshed)
        assert refreshed.plan == "free"


async def test_pre_checkout_handler_answers(sqlite_maker):
    from app.bot.handlers.stars import stars_pre_checkout

    async with sqlite_maker() as s:
        await _seed(s)
        ok_query = SimpleNamespace(
            invoice_payload="plan:plus", total_amount=50, currency="XTR",
            answer=AsyncMock(),
        )
        await stars_pre_checkout(ok_query, s)
        ok_query.answer.assert_awaited_once_with(ok=True)

        bad_query = SimpleNamespace(
            invoice_payload="plan:plus", total_amount=1, currency="XTR",
            answer=AsyncMock(),
        )
        await stars_pre_checkout(bad_query, s)
        args, kwargs = bad_query.answer.await_args
        assert kwargs["ok"] is False and kwargs["error_message"]


async def test_successful_payment_handler_replies(sqlite_maker):
    from app.bot.handlers.stars import stars_successful_payment

    async with sqlite_maker() as s:
        user = await _seed(s)
        message = SimpleNamespace(
            successful_payment=SimpleNamespace(
                invoice_payload="plan:plus",
                telegram_payment_charge_id="CHG-3",
                total_amount=50,
                currency="XTR",
            ),
            answer=AsyncMock(),
        )
        await stars_successful_payment(message, s, user)
        message.answer.assert_awaited()  # activation reply sent
        refreshed = await s.get(User, user.id)
        await s.refresh(refreshed)
        assert refreshed.plan == "plus"
