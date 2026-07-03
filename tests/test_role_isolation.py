"""H1 — role-isolation regression fence.

Proves a reseller/customer can NEVER reach platform-owner-only capabilities on
ANY surface — panel routes, the in-bot buy-a-bot seller flow, and the keyboards
that expose it — while the platform owner still can. A future change that opens
a hole must break one of these tests.

Platform-owner-only surfaces covered here:
  * panel: platform super-admin dashboard + tenants list, bot-creation/rental
    pricing (bot-plans), whole-DB backups, and license issuing/management;
  * bot: the seller flow (/newbot, the "ساخت ربات" button, plan pick, token
    intake) and the keyboard button that starts it.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.bot.handlers.newbot as newbot
from app.bot import messages
from app.bot.filters import IsPlatform
from app.bot.keyboards.reply import build_admin_menu, build_user_menu
from app.core.redis_client import get_redis
from app.core.tenant_context import (
    PLATFORM_TENANT_ID,
    all_tenants,
    is_platform,
    reset_tenant,
    set_tenant,
    tenant_scope,
)
from app.db.session import get_session
from app.models import Base, PanelUser, Tenant
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore

# Reseller tenant id used across the bot tests (anything that is NOT the
# platform tenant proves the isolation).
_RESELLER = 2


# --------------------------------------------------------------------------- #
#  Panel harness (SQLite + fakeredis + the real app via ASGI)
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
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
    """A super-admin (platform) panel user + a reseller (customer) panel user."""
    with all_tenants():
        async with Session() as s:
            cust_tenant = Tenant(bot_username="cust", bot_id=880088, status="active")
            s.add(cust_tenant)
            await s.commit()
            tid = cust_tenant.id
            root = PanelUser(
                username="root", password_hash=hash_password("pw"),
                tenant_id=PLATFORM_TENANT_ID, is_superadmin=True,
            )
            cust = PanelUser(
                username="cust", password_hash=hash_password("pw"),
                tenant_id=tid, is_superadmin=False,
            )
            s.add_all([root, cust])
            await s.commit()
            return {"tid": tid, "root": root.id, "cust": cust.id}


async def _client(app, uid):
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    return client, csrf


# Every platform-owner-only panel GET surface (the regression fence).
PLATFORM_ONLY_GET = [
    "/panel/platform",
    "/panel/platform/tenants",
    "/panel/bot-plans",
    "/panel/backups",
    "/panel/license",
]


async def test_reseller_refused_on_every_platform_only_panel_route(env):
    app, Session = env
    ids = await _seed(Session)
    client, _ = await _client(app, ids["cust"])
    try:
        for path in PLATFORM_ONLY_GET:
            resp = await client.get(path)
            assert resp.status_code == 403, f"{path} must be 403 for a reseller"
    finally:
        await client.aclose()


async def test_reseller_refused_on_platform_only_writes(env):
    """A reseller can't set bot pricing, manage the license, or suspend tenants."""
    app, Session = env
    ids = await _seed(Session)
    client, csrf = await _client(app, ids["cust"])
    try:
        posts = [
            ("/panel/license/activate", {"key": "ABC", "csrf_token": csrf}),
            (
                "/panel/bot-plans/save",
                {"key": "m", "title": "t", "price": "0",
                 "duration_days": "0", "csrf_token": csrf},
            ),
            (f"/panel/platform/tenants/{ids['tid']}/suspend", {"csrf_token": csrf}),
        ]
        for path, data in posts:
            resp = await client.post(path, data=data, follow_redirects=False)
            assert resp.status_code == 403, f"{path} must be 403 for a reseller"
    finally:
        await client.aclose()


async def test_platform_owner_allowed_on_platform_only_routes(env):
    app, Session = env
    ids = await _seed(Session)
    client, _ = await _client(app, ids["root"])
    try:
        for path in PLATFORM_ONLY_GET:
            resp = await client.get(path)
            assert resp.status_code == 200, f"{path} must be 200 for the platform owner"
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
#  Bot seller-flow harness (direct handler calls under a tenant context)
# --------------------------------------------------------------------------- #
def _fake_message(text: str = ""):
    return SimpleNamespace(
        text=text,
        answer=AsyncMock(),
        delete=AsyncMock(),
        from_user=SimpleNamespace(id=90001),
    )


def _fake_state(data: dict | None = None):
    return SimpleNamespace(
        clear=AsyncMock(),
        set_state=AsyncMock(),
        update_data=AsyncMock(),
        get_data=AsyncMock(return_value=data or {}),
    )


def _labels(markup):
    return [b.text for row in markup.keyboard for b in row]


def test_is_platform_guard_primitive():
    with tenant_scope(PLATFORM_TENANT_ID):
        assert is_platform() is True
    with tenant_scope(_RESELLER):
        assert is_platform() is False
    # the cross-tenant ALL_TENANTS bypass is NOT the platform
    with all_tenants():
        assert is_platform() is False
    # no context at all fails closed (override the autouse platform default)
    tok = set_tenant(None)
    try:
        assert is_platform() is False
    finally:
        reset_tenant(tok)


async def test_is_platform_filter_matches_only_platform():
    f = IsPlatform()
    with tenant_scope(PLATFORM_TENANT_ID):
        assert await f(SimpleNamespace()) is True
    with tenant_scope(_RESELLER):
        assert await f(SimpleNamespace()) is False


def test_create_bot_button_only_on_platform_keyboard():
    # The seller entrypoint button appears ONLY on the platform bot's menus.
    assert messages.BTN_CREATE_BOT in _labels(build_user_menu(is_platform=True))
    assert messages.BTN_CREATE_BOT not in _labels(build_user_menu(is_platform=False))
    assert messages.BTN_CREATE_BOT in _labels(
        build_admin_menu(is_owner=True, is_platform=True)
    )
    assert messages.BTN_CREATE_BOT not in _labels(
        build_admin_menu(is_owner=True, is_platform=False)
    )


async def test_newbot_command_refused_off_platform_shown_on_platform(monkeypatch):
    shown = {"n": 0}

    async def _fake_show(message, session, db_user):
        shown["n"] += 1

    monkeypatch.setattr(newbot, "_show_plans", _fake_show)

    # reseller: refused with the Persian message, plans are NEVER shown
    msg = _fake_message()
    with tenant_scope(_RESELLER):
        await newbot.newbot_command(msg, _fake_state(), session=object(), db_user=None)
    assert shown["n"] == 0
    msg.answer.assert_awaited_once_with(messages.NEWBOT_ONLY_PLATFORM)

    # platform owner: plans shown, no refusal
    msg2 = _fake_message()
    with tenant_scope(PLATFORM_TENANT_ID):
        await newbot.newbot_command(msg2, _fake_state(), session=object(), db_user=None)
    assert shown["n"] == 1
    msg2.answer.assert_not_awaited()


async def test_reseller_cannot_create_bot_at_token_step(monkeypatch):
    """The security-critical property: even reaching the token step off-platform
    creates NOTHING — no token validation, no tenant creation."""
    validated = {"v": False}
    created = {"v": False}

    async def _validate(token):
        validated["v"] = True
        return (999, "x")

    class _Svc:
        def __init__(self, *a, **k):
            pass

        async def create_from_wallet(self, **k):
            created["v"] = True
            return SimpleNamespace(status=newbot.BotCreationStatus.FAILED)

    monkeypatch.setattr(newbot, "validate_bot_token", _validate)
    monkeypatch.setattr(newbot, "BotCreationService", _Svc)

    msg = _fake_message(text="123:TOKEN")
    state = _fake_state(data={"plan_key": "perpetual"})
    with tenant_scope(_RESELLER):
        await newbot.newbot_receive_token(
            msg, state, session=object(),
            db_user=SimpleNamespace(id=1), registry=None,
        )
    assert validated["v"] is False   # never validated a token off-platform
    assert created["v"] is False     # never created a bot off-platform
    state.clear.assert_awaited()      # the flow was aborted


async def test_platform_owner_proceeds_at_token_step(monkeypatch):
    created = {"v": False}

    async def _validate(token):
        return (700700, "mybot")

    class _Svc:
        def __init__(self, *a, **k):
            pass

        async def create_from_wallet(self, **k):
            created["v"] = True
            return SimpleNamespace(status=newbot.BotCreationStatus.FAILED)

    monkeypatch.setattr(newbot, "validate_bot_token", _validate)
    monkeypatch.setattr(newbot, "BotCreationService", _Svc)

    msg = _fake_message(text="123:TOKEN")
    state = _fake_state(data={"plan_key": "perpetual"})
    with tenant_scope(PLATFORM_TENANT_ID):
        await newbot.newbot_receive_token(
            msg, state, session=object(),
            db_user=SimpleNamespace(id=1), registry=None,
        )
    assert created["v"] is True  # the platform owner CAN create a bot
