"""F5 — platform super-admin surface (SQLite + fakeredis, real app via ASGI).

Verifies cross-tenant management is reachable ONLY by a super-admin (customers
get 403), suspending removes the bot from F2's registry (stops serving), actions
are audited as platform actions (tenant_id NULL), and a decrypted bot token
never appears in any response.
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

import app.bot.registry as registry_mod
from app.bot.registry import BotRegistry, RegisteredBot
from app.core.redis_client import get_redis
from app.core.tenant_context import all_tenants
from app.db.session import get_session
from app.models import Base, PanelAudit, PanelUser, Tenant
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore
from app.services.tenant_service import TenantService

TOKEN_PLAINTEXT = "123456:SUPERSECRET_bot_token"


class FakeBot:
    def __init__(self, token):
        self.token = token
        self.set_webhook = AsyncMock()
        self.delete_webhook = AsyncMock()
        self.session = SimpleNamespace(close=AsyncMock())


@pytest_asyncio.fixture
async def env(monkeypatch):
    monkeypatch.setattr(registry_mod, "Bot", FakeBot)
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # seeds platform tenant 1
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
    with all_tenants():
        async with Session() as s:
            tenant = await TenantService(s).create(
                owner_user_id=5, bot_id=880088, bot_username="cust",
                bot_token=TOKEN_PLAINTEXT,
            )
            tid, bot_id = tenant.id, tenant.bot_id
            root = PanelUser(
                username="root", password_hash=hash_password("pw"),
                tenant_id=1, is_superadmin=True,
            )
            cust = PanelUser(
                username="cust", password_hash=hash_password("pw"),
                tenant_id=tid, is_superadmin=False,
            )
            s.add_all([root, cust])
            await s.commit()
            return {"tid": tid, "bot_id": bot_id, "root": root.id, "cust": cust.id}


async def _client(app, uid):
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    return client, csrf


async def test_superadmin_lists_tenants_without_token(env):
    app, Session = env
    ids = await _seed(Session)
    client, _ = await _client(app, ids["root"])
    try:
        resp = await client.get("/panel/platform/tenants")
        assert resp.status_code == 200
        assert "cust" in resp.text  # the bot username is shown
        # the decrypted token is NEVER rendered
        assert TOKEN_PLAINTEXT not in resp.text
        dash = await client.get("/panel/platform")
        assert dash.status_code == 200
    finally:
        await client.aclose()


async def test_customer_cannot_reach_platform(env):
    app, Session = env
    ids = await _seed(Session)
    client, _ = await _client(app, ids["cust"])
    try:
        for path in ("/panel/platform", "/panel/platform/tenants", "/panel/bot-plans"):
            resp = await client.get(path)
            assert resp.status_code == 403, path
    finally:
        await client.aclose()


async def test_suspend_removes_bot_from_registry_and_audits(env):
    app, Session = env
    ids = await _seed(Session)
    reg = BotRegistry(Session)
    reg._bots[ids["bot_id"]] = RegisteredBot(
        tenant_id=ids["tid"], bot_id=ids["bot_id"], bot=FakeBot("x"), secret="s"
    )
    app.state.registry = reg

    client, csrf = await _client(app, ids["root"])
    try:
        resp = await client.post(
            f"/panel/platform/tenants/{ids['tid']}/suspend",
            data={"csrf_token": csrf}, follow_redirects=False,
        )
        assert resp.status_code == 302
    finally:
        await client.aclose()

    # status flipped, bot removed from the registry (webhook deleted -> no serving)
    with all_tenants():
        async with Session() as s:
            assert (await TenantService(s).get(ids["tid"])).status == "suspended"
            audit = await s.scalar(
                select(PanelAudit).where(PanelAudit.action == "tenant_suspend")
            )
    assert reg.get(ids["bot_id"]) is None
    assert audit is not None and audit.target == str(ids["tid"])
    assert audit.tenant_id is None  # a platform (cross-tenant) action


async def test_reactivate_and_extend(env):
    app, Session = env
    ids = await _seed(Session)
    with all_tenants():
        async with Session() as s:
            await TenantService(s).set_status(ids["tid"], "suspended")
    app.state.registry = BotRegistry(Session)

    client, csrf = await _client(app, ids["root"])
    try:
        r1 = await client.post(
            f"/panel/platform/tenants/{ids['tid']}/reactivate",
            data={"csrf_token": csrf}, follow_redirects=False,
        )
        r2 = await client.post(
            f"/panel/platform/tenants/{ids['tid']}/extend",
            data={"days": "30", "csrf_token": csrf}, follow_redirects=False,
        )
        assert r1.status_code == 302 and r2.status_code == 302
    finally:
        await client.aclose()
    with all_tenants():
        async with Session() as s:
            t = await TenantService(s).get(ids["tid"])
    assert t.status == "active" and t.expires_at is not None
