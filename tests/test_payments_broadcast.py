"""I6 — broadcast filters + payment settings completion.

Broadcast plan filters (blocked always excluded); topup_min enforced; the
card_enabled toggle hides/blocks card top-up; the gateway recheck is idempotent
and can never manually credit a card payment.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.bot.handlers.billing as billing
from app.bot import messages
from app.bot.keyboards.inline import build_topup_methods
from app.core.redis_client import get_redis
from app.core.tenant_context import all_tenants, tenant_scope
from app.db.session import get_session
from app.models import Base, BroadcastRecipient, PanelAudit, PanelUser, Payment, Tenant, User
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore
from app.services import broadcast as bcast
from app.services.bot_setting_service import (
    KEY_CARD_ENABLED,
    KEY_CARD_NUMBER,
    BotSettingService,
)

T = 2


@pytest_asyncio.fixture
async def sm():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    with all_tenants():
        async with Session() as s:
            s.add(Tenant(id=T, bot_username="a", bot_id=2002, status="active"))
            await s.commit()
    try:
        yield Session
    finally:
        await engine.dispose()


# --- broadcast filters -----------------------------------------------------
async def test_broadcast_plan_filters_exclude_blocked(sm):
    with tenant_scope(T):
        async with sm() as s:
            s.add_all([
                User(telegram_id=1, plan="free", is_blocked=False),
                User(telegram_id=2, plan="plus", is_blocked=False),
                User(telegram_id=3, plan="max", is_blocked=False),
                User(telegram_id=4, plan="free", is_blocked=True),  # blocked -> never
            ])
            await s.commit()
        async with sm() as s:
            assert await bcast.audience_count(s, "all") == 3
            assert await bcast.audience_count(s, "free") == 1
            assert await bcast.audience_count(s, "premium") == 2
        async with sm() as s:
            job = await bcast.create_job(s, text="x", plan_filter="premium")
        async with sm() as s:
            tgs = set((await s.scalars(
                select(BroadcastRecipient.telegram_id).where(
                    BroadcastRecipient.broadcast_id == job.id
                )
            )).all())
    assert tgs == {2, 3}  # premium only, blocked excluded


# --- topup_min + card toggle ----------------------------------------------
class _FakeState:
    def __init__(self, data=None):
        self._data = data or {}

    async def get_data(self):
        return dict(self._data)

    async def clear(self):
        self._data = {}

    async def set_state(self, s):
        pass

    async def update_data(self, **kw):
        self._data.update(kw)


async def test_topup_min_enforced(sm):
    with tenant_scope(T):
        async with sm() as s:
            msg = SimpleNamespace(text="5000", answer=AsyncMock())  # < default 10000
            await billing.topup_amount(msg, _FakeState({"method": "card"}), s, SimpleNamespace(id=1))
    msg.answer.assert_awaited_once_with(messages.INVALID_AMOUNT)


async def test_card_available_respects_toggle(sm):
    with tenant_scope(T):
        async with sm() as s:
            await BotSettingService(s).set(KEY_CARD_NUMBER, "6037991122334455")
        async with sm() as s:
            assert await billing._card_available(s) is True  # set + enabled (default)
        async with sm() as s:
            await BotSettingService(s).set(KEY_CARD_ENABLED, False)
        async with sm() as s:
            assert await billing._card_available(s) is False  # toggle off blocks it


def test_topup_methods_hide_card_when_off():
    def labels(m):
        return [x.text for r in m.inline_keyboard for x in r]

    assert messages.BTN_PAY_CARD in labels(build_topup_methods(True, card=True))
    assert messages.BTN_PAY_CARD not in labels(build_topup_methods(True, card=False))


# --- gateway recheck (idempotent, never manual-credits a card) --------------
@pytest_asyncio.fixture
async def env(sm):
    Session = sm
    ids = {}
    with all_tenants():
        async with Session() as s:
            fin = PanelUser(username="fin", password_hash=hash_password("pw"),
                            tenant_id=T, role="finance", is_superadmin=False)
            s.add(fin)
            await s.commit()
            ids["fin"] = fin.id
    with tenant_scope(T):
        async with Session() as s:
            u = User(telegram_id=9001, balance=0)
            s.add(u)
            await s.flush()
            pay = Payment(user_id=u.id, amount=50000, method="card", status="pending", intent="topup")
            s.add(pay)
            await s.commit()
            ids["user"], ids["pay"] = u.id, pay.id
    from app.api.main import app

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield app, Session, ids
    finally:
        app.dependency_overrides.clear()


async def test_gateway_recheck_never_credits_a_card_payment(env):
    app, Session, ids = env
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": ids["fin"], "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    try:
        # recheck twice (idempotent): a card payment has no gateway -> "failed"
        for _ in range(2):
            r = await client.post(
                f"/panel/payments/{ids['pay']}/recheck",
                data={"csrf_token": csrf}, follow_redirects=False,
            )
            assert r.status_code == 302 and r.headers["location"].endswith("msg=failed")
    finally:
        await client.aclose()
    with tenant_scope(T):
        async with Session() as s:
            pay = await s.get(Payment, ids["pay"])
            user = await s.get(User, ids["user"])
            audits = [a.action for a in (await s.scalars(select(PanelAudit))).all()]
    assert pay.status == "pending"  # never approved via recheck
    assert user.balance == 0  # never credited
    assert audits.count("payment_recheck") == 2  # both attempts audited
