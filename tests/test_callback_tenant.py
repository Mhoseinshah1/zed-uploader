"""Fix-3 — HTTP callbacks + REST API resolve the tenant safely (no cross-tenant).

Pay returns resolve the tenant from the (tenant-scoped) payment row and run the
verify under it; ad clicks record under the ad's tenant; an unresolvable callback
fails closed; and /api/v1 is bound to the caller's tenant so it can't read
another tenant's data.
"""
from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core import jwt_utils
from app.core.tenant_context import all_tenants, current_tenant, tenant_scope
from app.db.session import get_session
from app.models import Ad, Base, Media, Payment, PanelUser, Tenant, User


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


async def _two_tenants(Session):
    with all_tenants():
        async with Session() as s:
            t2, t3 = Tenant(bot_username="c2", bot_id=2, status="active"), Tenant(bot_username="c3", bot_id=3, status="active")
            s.add_all([t2, t3])
            await s.commit()
            return t2.id, t3.id


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_pay_callback_resolves_payment_tenant(env, monkeypatch):
    app, Session = env
    t2, _ = await _two_tenants(Session)
    with tenant_scope(t2):
        async with Session() as s:
            u = User(telegram_id=222)
            s.add(u)
            await s.commit()
            pay = Payment(
                user_id=u.id, amount=5000, method="zarinpal", status="pending",
                authority="AUTH222", intent="topup",
            )
            s.add(pay)
            await s.commit()
            pay_id = pay.id

    seen = {}

    async def _stub_verify(session, pid):
        seen["tenant"] = current_tenant()  # must be the payment's tenant
        seen["pid"] = pid
        return "credited"

    monkeypatch.setattr("app.api.routes.pay.verify_order", _stub_verify)
    async with _client(app) as c:
        resp = await c.get("/pay/zarinpal/return", params={"Authority": "AUTH222", "Status": "OK"})
    assert resp.status_code == 200
    assert seen["tenant"] == t2 and seen["pid"] == pay_id  # scoped to the payment's tenant


async def test_pay_callback_unknown_fails_closed(env, monkeypatch):
    app, Session = env
    await _two_tenants(Session)
    called = {"v": False}

    async def _stub_verify(session, pid):
        called["v"] = True
        return "credited"

    monkeypatch.setattr("app.api.routes.pay.verify_order", _stub_verify)
    async with _client(app) as c:
        resp = await c.get("/pay/zarinpal/return", params={"Authority": "NOPE", "Status": "OK"})
    # unresolvable -> failed page, verify never runs (no cross-tenant guess)
    assert resp.status_code == 200 and called["v"] is False


async def test_ad_click_increments_under_right_tenant(env):
    app, Session = env
    t2, t3 = await _two_tenants(Session)
    with tenant_scope(t2):
        async with Session() as s:
            ad = Ad(title="a", text="x", placement="start_message", button_url="https://e/x")
            s.add(ad)
            await s.commit()
            ad_id = ad.id
    async with _client(app) as c:
        resp = await c.get(f"/ad/{ad_id}/click", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "https://e/x"
    # the click was recorded on tenant 2's ad (proves the route ran under t2)
    with tenant_scope(t2):
        async with Session() as s:
            assert (await s.get(Ad, ad_id)).click_count == 1


async def test_api_v1_scoped_to_caller_tenant(env):
    app, Session = env
    t2, t3 = await _two_tenants(Session)
    with all_tenants():
        async with Session() as s:
            pu2 = PanelUser(username="c2", password_hash="x", tenant_id=t2)
            s.add(pu2)
            await s.commit()
            pu2_id = pu2.id
    with tenant_scope(t2):
        async with Session() as s:
            s.add(Media(code="MINE2", status="approved"))
            await s.commit()
    with tenant_scope(t3):
        async with Session() as s:
            s.add(Media(code="THEIRS3", status="approved"))
            await s.commit()

    token = jwt_utils.encode(pu2_id)
    async with _client(app) as c:
        resp = await c.get("/api/v1/media", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    codes = [m["code"] for m in resp.json()["items"]]
    assert codes == ["MINE2"]  # only the caller's tenant, never THEIRS3
