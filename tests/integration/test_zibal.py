"""C1b money-safety integration tests (REAL Postgres, mocked Zibal HTTP).

Same guarantees as the other gateways through the shared seam: idempotent
verify (sequential AND concurrent double-return), Rial/Toman amount-mismatch
refusal, every credit through WalletService.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.models import Payment, User, WalletTransaction
from app.services.gateway_service import GatewayService
from app.services.providers import zibal as zibal_module
from app.services.providers.zibal import ZibalProvider
from app.services.wallet_service import WalletService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


class _FakeZibalHTTP:
    def __init__(self, verify_response: dict):
        self.verify_response = verify_response
        self.verify_calls = 0

    async def __call__(self, url, payload, timeout=20.0):
        if url.endswith("/request"):
            return {"result": 100, "trackId": 5551}
        if url.endswith("/verify"):
            self.verify_calls += 1
            return self.verify_response
        return {}


async def _seed_payment(maker, telegram_id, amount_toman, intent="topup"):
    async with maker() as s:
        user = User(telegram_id=telegram_id)
        s.add(user)
        await s.commit()
        payment = Payment(
            user_id=user.id, amount=amount_toman, method="zibal", provider="zibal",
            status="pending", intent=intent, authority="5551",
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
    return GatewayService(session, ZibalProvider("M-TEST"))


# credits exactly once; the verify response amount is Rial (x10) --------------
async def test_verify_credits_once_idempotent(pg_sessionmaker, monkeypatch):
    uid, pid = await _seed_payment(pg_sessionmaker, 401, 7000)  # 7000 Toman
    fake = _FakeZibalHTTP({"result": 100, "refNumber": 777, "amount": 70000})  # Rial
    monkeypatch.setattr(zibal_module, "post_json", fake)

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
    assert bal == 7000  # credited in Toman
    assert len(rows) == 1 and rows[0].reference == "zibal:777"
    assert pay.status == "approved" and pay.provider_ref == "777"


# concurrent double-return -> exactly one credit ------------------------------
async def test_verify_concurrent_double_return(pg_sessionmaker, monkeypatch):
    uid, pid = await _seed_payment(pg_sessionmaker, 402, 900)
    fake = _FakeZibalHTTP({"result": 100, "refNumber": 9, "amount": 9000})
    monkeypatch.setattr(zibal_module, "post_json", fake)

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
    uid, pid = await _seed_payment(pg_sessionmaker, 403, 4000)
    monkeypatch.setattr(
        zibal_module, "post_json", _FakeZibalHTTP({"result": 202})  # not paid
    )
    async with pg_sessionmaker() as s:
        assert await _gateway(s).verify_and_apply(pid) == "failed"
    async with pg_sessionmaker() as s:
        pay = await s.get(Payment, pid)
    assert pay.status == "pending"
    assert await _deposits(pg_sessionmaker, uid) == 0


# amount mismatch (gateway reports a different Rial amount) -> no credit ------
async def test_amount_mismatch_rejects(pg_sessionmaker, monkeypatch):
    uid, pid = await _seed_payment(pg_sessionmaker, 404, 5000)  # expect 50000 Rial
    monkeypatch.setattr(
        zibal_module,
        "post_json",
        _FakeZibalHTTP({"result": 100, "refNumber": 1, "amount": 40000}),  # 4000 Toman!
    )
    async with pg_sessionmaker() as s:
        assert await _gateway(s).verify_and_apply(pid) == "mismatch"
    async with pg_sessionmaker() as s:
        pay = await s.get(Payment, pid)
    assert pay.status == "rejected"
    assert await _deposits(pg_sessionmaker, uid) == 0
