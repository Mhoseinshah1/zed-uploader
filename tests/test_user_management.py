"""I3 — manual wallet + subscription management in /panel/users/{id}.

Wallet credit/debit carry a reason, a panel:<id> reference, a ledger row, an
audit entry, and a best-effort user notice. Subscription change/extend/lifetime/
cancel are audited. Role gating: wallet=owner/finance, subscription=owner/admin.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.panel.routes.users as users_routes
from app.core.redis_client import get_redis
from app.core.tenant_context import all_tenants, tenant_scope
from app.db.session import get_session
from app.models import Base, PanelAudit, PanelUser, Plan, Subscription, Tenant, User
from app.models.wallet import WalletTransaction
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore

T = 2


@pytest_asyncio.fixture
async def env(monkeypatch):
    # stub the best-effort notify so tests never build a real Bot
    notes = []

    async def _fake_notify(session, user_id, text):
        notes.append((user_id, text))
        return True

    monkeypatch.setattr(users_routes, "notify_user", _fake_notify)

    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    ids = {}
    with all_tenants():
        async with Session() as s:
            s.add(Tenant(id=T, bot_username="a", bot_id=2002, status="active"))
            await s.commit()
            for r in ("owner", "finance", "support", "admin"):
                pu = PanelUser(username=f"u_{r}", password_hash=hash_password("pw"),
                               tenant_id=T, role=r, is_superadmin=False)
                s.add(pu)
                await s.flush()
                ids[r] = pu.id
            await s.commit()
    with tenant_scope(T):
        async with Session() as s:
            u = User(telegram_id=5001, balance=0)
            s.add(u)
            s.add(Plan(key="pro", title="Pro", price=1000, duration_days=30, is_active=True))
            await s.commit()
            ids["user"] = u.id
    from app.api.main import app

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield app, Session, ids, notes
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def _client(app, uid):
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    return client, csrf


async def test_manual_credit_writes_ledger_reference_audit_notify(env):
    app, Session, ids, notes = env
    client, csrf = await _client(app, ids["finance"])
    try:
        r = await client.post(
            f"/panel/users/{ids['user']}/wallet",
            data={"direction": "credit", "amount": "2500", "reason": "جبران خطا", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert r.status_code == 302
    finally:
        await client.aclose()
    with tenant_scope(T):
        async with Session() as s:
            u = await s.get(User, ids["user"])
            tx = await s.scalar(
                select(WalletTransaction).where(WalletTransaction.user_id == ids["user"])
            )
            audit = await s.scalar(select(PanelAudit).where(PanelAudit.action == "wallet_credit"))
    assert u.balance == 2500  # ledger applied via WalletService
    assert tx.reference == f"panel:{ids['finance']}" and "جبران خطا" in tx.description
    assert audit is not None and "جبران خطا" in audit.target
    assert notes and notes[-1][0] == ids["user"]  # best-effort notice sent


async def test_manual_debit_and_insufficient(env):
    app, Session, ids, notes = env
    # first credit 1000
    client, csrf = await _client(app, ids["owner"])
    try:
        await client.post(
            f"/panel/users/{ids['user']}/wallet",
            data={"direction": "credit", "amount": "1000", "reason": "seed", "csrf_token": csrf},
            follow_redirects=False,
        )
        # debit 300 ok
        await client.post(
            f"/panel/users/{ids['user']}/wallet",
            data={"direction": "debit", "amount": "300", "reason": "کسر", "csrf_token": csrf},
            follow_redirects=False,
        )
        # debit 5000 -> insufficient (balance is 700)
        r = await client.post(
            f"/panel/users/{ids['user']}/wallet",
            data={"direction": "debit", "amount": "5000", "reason": "زیاد", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert r.headers["location"].endswith("msg=insufficient")
    finally:
        await client.aclose()
    with tenant_scope(T):
        async with Session() as s:
            assert (await s.get(User, ids["user"])).balance == 700  # 1000 - 300


async def test_large_amount_requires_confirmation(env):
    app, Session, ids, notes = env
    client, csrf = await _client(app, ids["finance"])
    try:
        # 10,000,000 >= LARGE_ADJUST and no confirm -> confirm page, NOT applied
        r = await client.post(
            f"/panel/users/{ids['user']}/wallet",
            data={"direction": "credit", "amount": "10000000", "reason": "بزرگ", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert r.status_code == 200 and "تأیید" in r.text
    finally:
        await client.aclose()
    with tenant_scope(T):
        async with Session() as s:
            assert (await s.get(User, ids["user"])).balance == 0  # nothing applied yet


async def test_subscription_actions_audited(env):
    app, Session, ids, notes = env
    client, csrf = await _client(app, ids["admin"])
    try:
        for action, data in [
            ("change", {"plan": "pro"}),
            ("extend", {"days": "15"}),
            ("lifetime", {}),
            ("cancel", {}),
        ]:
            r = await client.post(
                f"/panel/users/{ids['user']}/subscription",
                data={"action": action, "csrf_token": csrf, **data},
                follow_redirects=False,
            )
            assert r.status_code == 302, action
    finally:
        await client.aclose()
    with tenant_scope(T):
        async with Session() as s:
            actions = {a.action for a in (await s.scalars(select(PanelAudit))).all()}
            u = await s.get(User, ids["user"])
    assert {"subscription_change", "subscription_extend", "subscription_lifetime",
            "subscription_cancel"} <= actions
    assert u.plan == "free" and u.plan_expires_at is None  # cancel won


async def test_role_gating_wallet_and_subscription(env):
    app, Session, ids, notes = env
    # support cannot touch wallet or subscription
    client, csrf = await _client(app, ids["support"])
    try:
        w = await client.post(
            f"/panel/users/{ids['user']}/wallet",
            data={"direction": "credit", "amount": "1", "reason": "x", "csrf_token": csrf},
            follow_redirects=False,
        )
        sub = await client.post(
            f"/panel/users/{ids['user']}/subscription",
            data={"action": "lifetime", "csrf_token": csrf}, follow_redirects=False,
        )
        assert w.status_code == 403 and sub.status_code == 403
    finally:
        await client.aclose()
    # finance can do wallet but NOT subscription (subscription is owner/admin)
    client, csrf = await _client(app, ids["finance"])
    try:
        sub = await client.post(
            f"/panel/users/{ids['user']}/subscription",
            data={"action": "lifetime", "csrf_token": csrf}, follow_redirects=False,
        )
        assert sub.status_code == 403
    finally:
        await client.aclose()
