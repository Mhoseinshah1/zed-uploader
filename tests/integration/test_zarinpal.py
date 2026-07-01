"""C1 money-safety integration tests (REAL Postgres, mocked Zarinpal HTTP).

Same guarantees as CentralPay, now via the generic gateway seam: idempotent
verify (sequential AND concurrent double-return), amount-mismatch refusal, and
plan-intent activation — every credit through WalletService.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.models import Payment, Plan, User, WalletTransaction
from app.services.gateway_service import GatewayService
from app.services.providers import upsert_config, verify_order
from app.services.providers import zarinpal as zarinpal_module
from app.services.providers.base import PaymentProvider, VerifyResult
from app.services.providers.zarinpal import ZarinpalProvider
from app.services.wallet_service import WalletService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


class _FakeZarinpalHTTP:
    """Records calls and returns canned request/verify responses."""

    def __init__(self, verify_response: dict):
        self.verify_response = verify_response
        self.verify_calls = 0

    async def __call__(self, url, payload, timeout=20.0):
        if url.endswith("PaymentRequest.json"):
            return {"Status": 100, "Authority": "AUTH-1"}
        if url.endswith("PaymentVerification.json"):
            self.verify_calls += 1
            return self.verify_response
        return {}


async def _seed_payment(maker, telegram_id, amount, intent="topup"):
    async with maker() as s:
        user = User(telegram_id=telegram_id)
        s.add(user)
        await s.commit()
        payment = Payment(
            user_id=user.id, amount=amount, method="zarinpal", provider="zarinpal",
            status="pending", intent=intent, authority="AUTH-1",
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


def _gateway(session):
    return GatewayService(session, ZarinpalProvider("M-TEST"))


# credits exactly once; second call is "already" without a second HTTP verify -
async def test_verify_credits_once_idempotent(pg_sessionmaker, monkeypatch):
    uid, pid = await _seed_payment(pg_sessionmaker, 301, 7000)
    fake = _FakeZarinpalHTTP({"Status": 100, "RefID": 424242})
    monkeypatch.setattr(zarinpal_module, "post_json", fake)

    async with pg_sessionmaker() as s:
        r1 = await _gateway(s).verify_and_apply(pid)
    async with pg_sessionmaker() as s:
        r2 = await _gateway(s).verify_and_apply(pid)

    assert r1 == "credited" and r2 == "already"
    assert fake.verify_calls == 1
    async with pg_sessionmaker() as s:
        bal = await WalletService(s).balance(uid)
        rows = (await s.scalars(select(WalletTransaction).where(
            WalletTransaction.user_id == uid))).all()
        pay = await s.get(Payment, pid)
    assert bal == 7000
    assert len(rows) == 1 and rows[0].reference == "zarinpal:424242"
    assert pay.status == "approved" and pay.provider_ref == "424242"


# concurrent double-return -> exactly one credit ------------------------------
async def test_verify_concurrent_double_return(pg_sessionmaker, monkeypatch):
    uid, pid = await _seed_payment(pg_sessionmaker, 302, 900)
    fake = _FakeZarinpalHTTP({"Status": 100, "RefID": 5})
    monkeypatch.setattr(zarinpal_module, "post_json", fake)

    async def do():
        async with pg_sessionmaker() as s:
            return await _gateway(s).verify_and_apply(pid)

    results = await asyncio.gather(do(), do(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"raised under concurrency: {errors}"
    assert sorted(results) == ["already", "credited"]
    assert fake.verify_calls == 1
    assert await _deposits(pg_sessionmaker, uid) == 1


# gateway says not paid -> failed, stays pending, no credit -------------------
async def test_verify_not_paid_no_credit(pg_sessionmaker, monkeypatch):
    uid, pid = await _seed_payment(pg_sessionmaker, 303, 4000)
    monkeypatch.setattr(
        zarinpal_module, "post_json", _FakeZarinpalHTTP({"Status": -21})
    )
    async with pg_sessionmaker() as s:
        assert await _gateway(s).verify_and_apply(pid) == "failed"
    async with pg_sessionmaker() as s:
        pay = await s.get(Payment, pid)
    assert pay.status == "pending"  # retry allowed
    assert await _deposits(pg_sessionmaker, uid) == 0


# generic amount-mismatch guard -> rejected, no credit ------------------------
class _LyingProvider(PaymentProvider):
    key = "zarinpal"  # matches the seeded payment's method
    title = "Lying"

    async def create(self, payment):  # pragma: no cover - unused
        return None

    async def verify(self, payment):
        return VerifyResult(ok=True, amount=payment.amount - 1, ref="X")


async def test_amount_mismatch_rejects(pg_sessionmaker):
    uid, pid = await _seed_payment(pg_sessionmaker, 304, 5000)
    async with pg_sessionmaker() as s:
        result = await GatewayService(s, _LyingProvider()).verify_and_apply(pid)
    assert result == "mismatch"
    async with pg_sessionmaker() as s:
        pay = await s.get(Payment, pid)
    assert pay.status == "rejected"
    assert await _deposits(pg_sessionmaker, uid) == 0


# verify_order dispatches by the payment's provider, even when disabled ------
async def test_verify_order_dispatch_and_disabled_provider(pg_sessionmaker, monkeypatch):
    uid, pid = await _seed_payment(pg_sessionmaker, 305, 2500)
    fake = _FakeZarinpalHTTP({"Status": 100, "RefID": 88})
    monkeypatch.setattr(zarinpal_module, "post_json", fake)
    async with pg_sessionmaker() as s:
        # merchant configured but owner has DISABLED the provider: an in-flight
        # payment must still verify (enable switch only gates new payments)
        await upsert_config(s, "zarinpal", is_enabled=False, merchant_id="M-TEST")

    async with pg_sessionmaker() as s:
        assert await verify_order(s, pid) == "credited"
    assert await _deposits(pg_sessionmaker, uid) == 1


# plan intent activates through the shared seam -------------------------------
async def test_plan_intent_activates(pg_sessionmaker, monkeypatch):
    async with pg_sessionmaker() as s:
        s.add(Plan(key="plus", title="Plus", price=2500, duration_days=30, max_files=100))
        await s.commit()
    uid, pid = await _seed_payment(pg_sessionmaker, 306, 2500, intent="plan:plus")
    monkeypatch.setattr(
        zarinpal_module, "post_json", _FakeZarinpalHTTP({"Status": 100, "RefID": 9})
    )
    async with pg_sessionmaker() as s:
        assert await _gateway(s).verify_and_apply(pid) == "credited"
    async with pg_sessionmaker() as s:
        user = await s.get(User, uid)
        bal = await WalletService(s).balance(uid)
    assert user.plan == "plus" and user.plan_expires_at is not None
    assert bal == 0  # deposited then spent on the plan
