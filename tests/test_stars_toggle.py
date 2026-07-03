"""I4 — Telegram Stars global toggle + install.sh webhook fix.

Disabled: the Stars button is hidden, StarsBuyCb is rejected, and pre_checkout
is refused (an old invoice can't complete). Enabled + a plan stars_price works.
The panel toggle is audited. install.sh sets pre_checkout_query in
allowed_updates.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.bot.handlers.stars as stars_h
from app.bot import messages
from app.bot.keyboards.inline import build_buy_confirm
from app.core.redis_client import get_redis
from app.core.tenant_context import all_tenants, tenant_scope
from app.db.session import get_session
from app.models import Base, PanelAudit, PanelUser, Plan, Tenant
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore
from app.services.bot_setting_service import KEY_STARS_ENABLED, BotSettingService

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


async def _true(session):
    return True


# --- setting + keyboard ----------------------------------------------------
async def test_stars_enabled_default_true_and_toggle(sm):
    with tenant_scope(T):
        async with sm() as s:
            assert await BotSettingService(s).stars_enabled() is True  # default on
        async with sm() as s:
            await BotSettingService(s).set(KEY_STARS_ENABLED, False)
        async with sm() as s:
            assert await BotSettingService(s).stars_enabled() is False


def test_build_buy_confirm_hides_stars_when_off():
    def labels(m):
        return [b.text for row in m.inline_keyboard for b in row]

    assert messages.BTN_PAY_STARS in labels(build_buy_confirm("pro", False, stars=True))
    assert messages.BTN_PAY_STARS not in labels(build_buy_confirm("pro", False, stars=False))


# --- handlers --------------------------------------------------------------
async def test_stars_buy_rejected_when_disabled(sm, monkeypatch):
    monkeypatch.setattr("app.services.license_service.paid_features_allowed", _true)
    with tenant_scope(T):
        async with sm() as s:
            await BotSettingService(s).set(KEY_STARS_ENABLED, False)
    cb = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer_invoice=AsyncMock()))
    with tenant_scope(T):
        async with sm() as s:
            await stars_h.stars_buy(cb, SimpleNamespace(plan="pro"), s)
    cb.answer.assert_awaited_once_with(messages.STARS_DISABLED, show_alert=True)
    cb.message.answer_invoice.assert_not_awaited()


async def test_pre_checkout_rejected_when_disabled(sm):
    with tenant_scope(T):
        async with sm() as s:
            await BotSettingService(s).set(KEY_STARS_ENABLED, False)
    q = SimpleNamespace(answer=AsyncMock(), invoice_payload="plan:pro", total_amount=50, currency="XTR")
    with tenant_scope(T):
        async with sm() as s:
            await stars_h.stars_pre_checkout(q, s)
    q.answer.assert_awaited_once_with(ok=False, error_message=messages.STARS_DISABLED)


async def test_stars_buy_proceeds_when_enabled(sm, monkeypatch):
    monkeypatch.setattr("app.services.license_service.paid_features_allowed", _true)
    with tenant_scope(T):
        async with sm() as s:
            s.add(Plan(key="pro", title="Pro", price=1000, duration_days=30,
                       stars_price=50, is_active=True))
            await s.commit()
    cb = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer_invoice=AsyncMock()))
    with tenant_scope(T):  # stars_enabled defaults True
        async with sm() as s:
            await stars_h.stars_buy(cb, SimpleNamespace(plan="pro"), s)
    cb.answer.assert_awaited_once_with()  # reached the end, not rejected


# --- panel toggle (audited) ------------------------------------------------
@pytest_asyncio.fixture
async def env(sm):
    Session = sm
    with all_tenants():
        async with Session() as s:
            owner = PanelUser(username="owner", password_hash=hash_password("pw"),
                              tenant_id=T, role="owner", is_superadmin=False)
            s.add(owner)
            await s.commit()
            oid = owner.id
    from app.api.main import app

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield app, Session, oid
    finally:
        app.dependency_overrides.clear()


async def test_panel_stars_toggle_audited(env):
    app, Session, oid = env
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": oid, "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    try:
        off = await client.post("/panel/providers/stars", data={"csrf_token": csrf}, follow_redirects=False)
        assert off.status_code == 302
        on = await client.post("/panel/providers/stars", data={"enabled": "on", "csrf_token": csrf}, follow_redirects=False)
        assert on.status_code == 302
    finally:
        await client.aclose()
    with tenant_scope(T):
        async with Session() as s:
            acts = [a.target for a in (await s.scalars(
                select(PanelAudit).where(PanelAudit.action == "stars_toggle")
            )).all()]
            assert await BotSettingService(s).stars_enabled() is True  # last write = on
    assert acts == ["off", "on"]  # both toggles audited


# --- install.sh ------------------------------------------------------------
def test_install_sh_allowed_updates_includes_pre_checkout():
    src = open("install.sh").read()
    assert '"message","callback_query","pre_checkout_query"' in src
    # no stale 2-item allowed_updates remains
    assert '\'allowed_updates=["message","callback_query"]\'' not in src


def test_install_sh_syntax_ok():
    r = subprocess.run(["bash", "-n", "install.sh"], capture_output=True)
    assert r.returncode == 0, r.stderr
