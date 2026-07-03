"""H3 — reseller management tools (platform side).

Per-reseller detail view, extend-reactivates-a-suspended tenant (+ re-registers
the bot), and the reseller broadcast (to all bot owners). All platform-only
(customer 403) and audited. The broadcast's exactly-once delivery is covered by
tests/integration/test_reseller_broadcast.py.
"""
from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.redis_client import get_redis
from app.core.tenant_context import all_tenants, tenant_scope
from app.db.session import get_session
from app.models import (
    Base,
    BroadcastJob,
    BroadcastRecipient,
    PanelAudit,
    PanelUser,
    Tenant,
    User,
)
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore


class FakeRegistry:
    def __init__(self):
        self.reloaded = []

    async def reload(self, tenant_id):
        self.reloaded.append(tenant_id)


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    from app.api.main import app

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield app, Session
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def _seed(Session):
    """Two reseller tenants (each with a platform-user owner) + a suspended one."""
    with tenant_scope(1):
        async with Session() as s:
            o1, o2 = User(telegram_id=7001), User(telegram_id=7002)
            plain = User(telegram_id=7999)  # a platform user who owns nothing
            s.add_all([o1, o2, plain])
            await s.commit()
            owners = (o1.id, o2.id)
    with all_tenants():
        async with Session() as s:
            s.add_all([
                Tenant(id=2, bot_username="acmebot", bot_id=2002, status="active", owner_user_id=owners[0]),
                Tenant(id=3, bot_username="betabot", bot_id=3003, status="active", owner_user_id=owners[1]),
                Tenant(id=4, bot_username="suspbot", bot_id=4004, status="suspended", owner_user_id=owners[0]),
            ])
            await s.commit()
            root = PanelUser(username="root", password_hash=hash_password("pw"),
                             tenant_id=1, is_superadmin=True)
            cust = PanelUser(username="cust", password_hash=hash_password("pw"),
                             tenant_id=2, is_superadmin=False)
            s.add_all([root, cust])
            await s.commit()
            return {"root": root.id, "cust": cust.id, "owners": owners}


async def _client(app, uid):
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    return client, csrf


async def test_customer_cannot_access_reseller_tools(env):
    app, Session = env
    ids = await _seed(Session)
    client, csrf = await _client(app, ids["cust"])
    try:
        assert (await client.get("/panel/platform/tenants/2")).status_code == 403
        assert (await client.get("/panel/platform/broadcast")).status_code == 403
        r = await client.post(
            "/panel/platform/broadcast",
            data={"text": "hi", "confirm": "1", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert r.status_code == 403
    finally:
        await client.aclose()


async def test_detail_view_for_superadmin(env):
    app, Session = env
    ids = await _seed(Session)
    # give tenant 2 some usage
    with tenant_scope(2):
        async with Session() as s:
            s.add(User(telegram_id=22001))
            await s.commit()
    client, _ = await _client(app, ids["root"])
    try:
        resp = await client.get("/panel/platform/tenants/2")
        assert resp.status_code == 200 and "acmebot" in resp.text
    finally:
        await client.aclose()


async def test_extend_reactivates_suspended_and_reregisters(env):
    app, Session = env
    ids = await _seed(Session)
    app.state.registry = FakeRegistry()
    client, csrf = await _client(app, ids["root"])
    try:
        resp = await client.post(
            "/panel/platform/tenants/4/extend",
            data={"days": "30", "csrf_token": csrf}, follow_redirects=False,
        )
        assert resp.status_code == 302
    finally:
        await client.aclose()
    with all_tenants():
        async with Session() as s:
            t = await s.get(Tenant, 4)
            assert t.status == "active" and t.expires_at is not None
            actions = [a.action for a in (await s.scalars(select(PanelAudit))).all()]
    assert "tenant_extend" in actions and "tenant_reactivate" in actions
    assert 4 in app.state.registry.reloaded  # bot re-registered


async def test_reseller_broadcast_snapshots_owners_once(env):
    app, Session = env
    ids = await _seed(Session)
    client, csrf = await _client(app, ids["root"])
    try:
        # without confirm -> shows the confirm page, creates nothing
        pre = await client.post(
            "/panel/platform/broadcast",
            data={"text": "به همه", "csrf_token": csrf},
        )
        assert pre.status_code == 200
        # confirm -> creates exactly one exactly-once job
        sent = await client.post(
            "/panel/platform/broadcast",
            data={"text": "به همه", "confirm": "1", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert sent.status_code == 302
    finally:
        await client.aclose()
    # one job under the platform tenant, one recipient per DISTINCT owner (2, not 3)
    with tenant_scope(1):
        async with Session() as s:
            jobs = (await s.scalars(select(BroadcastJob))).all()
            assert len(jobs) == 1 and jobs[0].total == 2
            recips = await s.scalar(
                select(func.count(BroadcastRecipient.id)).where(
                    BroadcastRecipient.broadcast_id == jobs[0].id
                )
            )
            tgs = set(
                (await s.scalars(select(BroadcastRecipient.telegram_id))).all()
            )
    assert recips == 2 and tgs == {7001, 7002}  # owners only, deduped
