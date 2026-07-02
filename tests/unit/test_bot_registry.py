"""F2 tests — multi-bot registry + per-tenant webhook routing (mock Telegram).

No real Telegram I/O: ``app.bot.registry.Bot`` is monkeypatched to a fake that
records set_webhook/delete_webhook. SQLite + fakeredis; the per-tenant route is
driven via ASGITransport against the real app with a hand-built registry.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.bot.registry as registry_mod
from app.bot.middlewares.tenant import TenantContextMiddleware
from app.bot.registry import BotRegistry, tenant_webhook_url
from app.core.tenant_context import all_tenants, current_tenant
from app.models import Base, Tenant
from app.services.tenant_service import TenantService


class FakeBot:
    """Stand-in for aiogram Bot — records webhook calls, no network."""

    instances: list["FakeBot"] = []

    def __init__(self, token: str) -> None:
        self.token = token
        self.set_webhook = AsyncMock()
        self.delete_webhook = AsyncMock()
        self.session = SimpleNamespace(close=AsyncMock())
        FakeBot.instances.append(self)


@pytest.fixture(autouse=True)
def _fake_bot(monkeypatch):
    FakeBot.instances = []
    monkeypatch.setattr(registry_mod, "Bot", FakeBot)


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_tenant(maker, *, bot_id=8001, secret=None, status="active") -> int:
    with all_tenants():
        async with maker() as s:
            t = await TenantService(s).create(
                owner_user_id=10, bot_id=bot_id, bot_username="cust",
                bot_token="123456:CUSTTOKEN", status=status,
            )
            if secret:
                t.webhook_secret = secret
                await s.commit()
            return t.id


# --- registry --------------------------------------------------------------
async def test_register_sets_webhook_and_generates_secret(maker):
    tid = await _make_tenant(maker, bot_id=8001, secret=None)
    reg = BotRegistry(maker)
    with all_tenants():
        async with maker() as s:
            tenant = await TenantService(s).get(tid)
            entry = await reg.register(s, tenant)

    assert entry is not None and entry.bot_id == 8001 and entry.tenant_id == tid
    assert reg.get(8001) is entry
    # webhook set to the platform-domain per-bot URL with the tenant's secret
    bot = FakeBot.instances[-1]
    bot.set_webhook.assert_awaited_once()
    kwargs = bot.set_webhook.await_args.kwargs
    assert kwargs["url"] == tenant_webhook_url(8001)
    assert kwargs["secret_token"] == entry.secret and entry.secret
    # a secret was generated AND persisted
    with all_tenants():
        async with maker() as s:
            assert (await TenantService(s).get(tid)).webhook_secret == entry.secret


async def test_platform_and_tokenless_tenants_are_not_registered(maker):
    reg = BotRegistry(maker)
    with all_tenants():
        async with maker() as s:
            platform = await TenantService(s).get(1)  # seeded, no bot_id/token
            assert await reg.register(s, platform) is None
    assert reg.bot_ids() == []


async def test_bad_token_suspends_tenant_never_raises(maker):
    tid = await _make_tenant(maker, bot_id=8002)
    reg = BotRegistry(maker)
    with all_tenants():
        async with maker() as s:
            tenant = await TenantService(s).get(tid)
            # make set_webhook blow up like a bad token would
            def boom(token):
                b = FakeBot(token)
                b.set_webhook = AsyncMock(side_effect=RuntimeError("401 Unauthorized"))
                return b
            import app.bot.registry as rm
            rm.Bot = boom
            entry = await reg.register(s, tenant)
    assert entry is None and reg.get(8002) is None
    with all_tenants():
        async with maker() as s:
            assert (await TenantService(s).get(tid)).status == "suspended"


async def test_load_active_then_unregister(maker):
    await _make_tenant(maker, bot_id=8003, secret="s3")
    reg = BotRegistry(maker)
    await reg.load_active()
    assert reg.get(8003) is not None
    bot = reg.get(8003).bot
    await reg.unregister(8003)
    assert reg.get(8003) is None
    bot.delete_webhook.assert_awaited_once()  # webhook removed -> stops serving


async def test_reload_registers_active_and_drops_suspended(maker):
    tid = await _make_tenant(maker, bot_id=8004, secret="s4")
    reg = BotRegistry(maker)
    await reg.reload(tid)
    assert reg.get(8004) is not None
    # suspend the tenant, reload -> unregistered
    with all_tenants():
        async with maker() as s:
            await TenantService(s).set_status(tid, "suspended")
    await reg.reload(tid)
    assert reg.get(8004) is None


# --- middleware ------------------------------------------------------------
async def test_middleware_sets_tenant_from_feed_update_data():
    mw = TenantContextMiddleware()
    seen = {}

    async def handler(event, data):
        seen["tenant"] = current_tenant()

    await mw(handler, object(), {"tenant_id": 77})
    assert seen["tenant"] == 77
    # no tenant_id -> defaults to the platform tenant
    seen.clear()
    await mw(handler, object(), {})
    assert seen["tenant"] == 1


# --- per-tenant webhook route ----------------------------------------------
async def _route_client(entry):
    from app.api.main import app

    reg = BotRegistry(async_sessionmaker(create_async_engine("sqlite+aiosqlite://")))
    if entry is not None:
        reg._bots[entry.bot_id] = entry
    app.state.registry = reg
    app.state.dp = SimpleNamespace(feed_update=AsyncMock())
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    return app, client


async def test_route_dispatches_with_tenant_context():
    from app.bot.registry import RegisteredBot

    entry = RegisteredBot(tenant_id=42, bot_id=9001, bot=FakeBot("x"), secret="topsecret")
    app, client = await _route_client(entry)
    try:
        resp = await client.post(
            "/tenant/9001/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "topsecret"},
            json={"update_id": 1},
        )
        assert resp.status_code == 200
        app.state.dp.feed_update.assert_awaited_once()
        # dispatched to the tenant's bot, tagged with its tenant id
        assert app.state.dp.feed_update.await_args.kwargs["tenant_id"] == 42
        assert app.state.dp.feed_update.await_args.args[0] is entry.bot
    finally:
        await client.aclose()


async def test_route_rejects_wrong_and_missing_secret():
    from app.bot.registry import RegisteredBot

    entry = RegisteredBot(tenant_id=42, bot_id=9002, bot=FakeBot("x"), secret="right")
    app, client = await _route_client(entry)
    try:
        bad = await client.post(
            "/tenant/9002/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json={"update_id": 1},
        )
        assert bad.status_code == 403
        missing = await client.post("/tenant/9002/webhook", json={"update_id": 1})
        assert missing.status_code == 403
        app.state.dp.feed_update.assert_not_awaited()
    finally:
        await client.aclose()


async def test_route_unknown_bot_is_404():
    app, client = await _route_client(None)
    try:
        resp = await client.post(
            "/tenant/12345/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "x"},
            json={"update_id": 1},
        )
        assert resp.status_code == 404
        app.state.dp.feed_update.assert_not_awaited()
    finally:
        await client.aclose()
