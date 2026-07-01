"""Phase 5 money-safety integration tests (REAL Postgres, mocked CentralPay HTTP).

Proves verify_and_apply is idempotent (sequential AND concurrent), amount-checked,
and that a plan intent activates after deposit — all through WalletService.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.models import Payment, Plan, User, WalletTransaction
from app.services import centralpay_service
from app.services.centralpay_service import CentralPayService
from app.services.wallet_service import WalletService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


class _FakeHTTP:
    """Records calls and returns canned getLink/verify responses."""

    def __init__(self, verify_response: dict):
        self.verify_response = verify_response
        self.verify_calls = 0
        self.getlink_calls = 0

    async def __call__(self, url, payload, timeout=20.0):
        if "getLink" in url:
            self.getlink_calls += 1
            return {"success": True, "data": {"redirectUrl": "https://pay/x"}}
        if "verify" in url:
            self.verify_calls += 1
            return self.verify_response
        return {"success": False, "data": {}}


def _ok_verify(amount: int, user_id: int, ref: int = 9001) -> dict:
    return {
        "success": True,
        "data": {"referenceId": ref, "amount": amount, "userId": user_id,
                 "userCardNumber": 6037000000000000},
    }


async def _seed_payment(maker, telegram_id, amount, intent="topup"):
    async with maker() as s:
        user = User(telegram_id=telegram_id)
        s.add(user)
        await s.commit()
        payment = Payment(
            user_id=user.id, amount=amount, method="centralpay",
            status="pending", intent=intent,
        )
        s.add(payment)
        await s.commit()
        return user.id, payment.id


async def _deposits(maker, uid):
    async with maker() as s:
        return int(
            await s.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.user_id == uid,
                    WalletTransaction.type == "deposit",
                )
            )
        )


# credits exactly once on success; ledger invariant holds ------------------
async def test_verify_credits_once(pg_sessionmaker, monkeypatch):
    uid, pid = await _seed_payment(pg_sessionmaker, 201, 5000)
    monkeypatch.setattr(centralpay_service, "post_json", _FakeHTTP(_ok_verify(5000, uid)))

    async with pg_sessionmaker() as s:
        result = await CentralPayService(s).verify_and_apply(pid)
    assert result == "credited"

    async with pg_sessionmaker() as s:
        bal = await WalletService(s).balance(uid)
        rows = (await s.scalars(select(WalletTransaction).where(
            WalletTransaction.user_id == uid))).all()
        pay = await s.get(Payment, pid)
    assert bal == 5000
    assert len(rows) == 1 and rows[0].reference == "centralpay:9001"
    assert rows[0].balance_after == 5000
    assert pay.status == "approved" and pay.provider_ref == "9001"


# second call returns "already" WITHOUT a second verify HTTP call ----------
async def test_verify_idempotent_sequential(pg_sessionmaker, monkeypatch):
    uid, pid = await _seed_payment(pg_sessionmaker, 202, 3000)
    fake = _FakeHTTP(_ok_verify(3000, uid))
    monkeypatch.setattr(centralpay_service, "post_json", fake)

    async with pg_sessionmaker() as s:
        r1 = await CentralPayService(s).verify_and_apply(pid)
    async with pg_sessionmaker() as s:
        r2 = await CentralPayService(s).verify_and_apply(pid)

    assert r1 == "credited" and r2 == "already"
    assert fake.verify_calls == 1  # no second verify for an already-paid order
    assert await _deposits(pg_sessionmaker, uid) == 1


# concurrent verify: credited once, verify called once --------------------
async def test_verify_idempotent_concurrent(pg_sessionmaker, monkeypatch):
    uid, pid = await _seed_payment(pg_sessionmaker, 203, 800)
    fake = _FakeHTTP(_ok_verify(800, uid))
    monkeypatch.setattr(centralpay_service, "post_json", fake)

    async def do():
        async with pg_sessionmaker() as s:
            return await CentralPayService(s).verify_and_apply(pid)

    results = await asyncio.gather(do(), do(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"raised under concurrency: {errors}"

    async with pg_sessionmaker() as s:
        bal = await WalletService(s).balance(uid)
    assert bal == 800
    assert await _deposits(pg_sessionmaker, uid) == 1
    assert fake.verify_calls == 1
    assert sorted(results) == ["already", "credited"]


# amount mismatch -> rejected, credits nothing ----------------------------
async def test_verify_amount_mismatch_rejects(pg_sessionmaker, monkeypatch):
    uid, pid = await _seed_payment(pg_sessionmaker, 204, 5000)
    # gateway reports a DIFFERENT amount than our order
    monkeypatch.setattr(centralpay_service, "post_json", _FakeHTTP(_ok_verify(4000, uid)))

    async with pg_sessionmaker() as s:
        result = await CentralPayService(s).verify_and_apply(pid)
    assert result == "mismatch"

    async with pg_sessionmaker() as s:
        bal = await WalletService(s).balance(uid)
        pay = await s.get(Payment, pid)
    assert bal == 0
    assert await _deposits(pg_sessionmaker, uid) == 0
    assert pay.status == "rejected"


# intent plan:plus activates the plan after deposit -----------------------
async def test_verify_plan_intent_activates(pg_sessionmaker, monkeypatch):
    async with pg_sessionmaker() as s:
        s.add(Plan(key="plus", title="Plus", price=3000, duration_days=30, max_files=100))
        await s.commit()
    uid, pid = await _seed_payment(pg_sessionmaker, 205, 3000, intent="plan:plus")
    monkeypatch.setattr(centralpay_service, "post_json", _FakeHTTP(_ok_verify(3000, uid)))

    async with pg_sessionmaker() as s:
        result = await CentralPayService(s).verify_and_apply(pid)
    assert result == "credited"

    async with pg_sessionmaker() as s:
        user = await s.get(User, uid)
        bal = await WalletService(s).balance(uid)
        deposits = int(await s.scalar(select(func.count(WalletTransaction.id)).where(
            WalletTransaction.user_id == uid, WalletTransaction.type == "deposit")))
        purchases = int(await s.scalar(select(func.count(WalletTransaction.id)).where(
            WalletTransaction.user_id == uid, WalletTransaction.type == "purchase")))
    assert user.plan == "plus" and user.plan_expires_at is not None
    assert bal == 0  # 3000 deposited, 3000 spent on the plan
    assert deposits == 1 and purchases == 1
