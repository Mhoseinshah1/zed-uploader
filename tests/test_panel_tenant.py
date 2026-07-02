"""F4 — per-customer panel isolation (SQLite + fakeredis, real app via ASGI).

Two customers with separate logins must each see ONLY their own tenant across
every panel section, a crafted request for another tenant's row must not leak,
and audit rows must carry the acting tenant. Exercises the F1 guard end-to-end
through the real panel routes (require_panel_user binds the login's tenant).
"""
from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.redis_client import get_redis
from app.core.tenant_context import all_tenants, tenant_scope
from app.db.session import get_session
from app.models import Base, Media, PanelAudit, PanelUser, Tenant, User
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore


@pytest_asyncio.fixture
async def env():
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


async def _client(app, uid):
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    return client, csrf


async def _seed(Session):
    with all_tenants():
        async with Session() as s:
            t2, t3 = Tenant(bot_username="c2", status="active"), Tenant(bot_username="c3", status="active")
            s.add_all([t2, t3])
            await s.commit()
            t2id, t3id = t2.id, t3.id
            pu2 = PanelUser(username="cust2", password_hash=hash_password("pw"), tenant_id=t2id)
            pu3 = PanelUser(username="cust3", password_hash=hash_password("pw"), tenant_id=t3id)
            s.add_all([pu2, pu3])
            await s.commit()
            ids = {"t2": t2id, "t3": t3id, "u2": pu2.id, "u3": pu3.id}
    with tenant_scope(t2id):
        async with Session() as s:
            s.add_all([Media(code="MEDIA2", status="approved"), User(telegram_id=2222)])
            await s.commit()
            ids["m2"] = await s.scalar(select(Media.id).where(Media.code == "MEDIA2"))
    with tenant_scope(t3id):
        async with Session() as s:
            s.add_all([Media(code="MEDIA3", status="approved"), User(telegram_id=3333)])
            await s.commit()
            ids["m3"] = await s.scalar(select(Media.id).where(Media.code == "MEDIA3"))
    return ids


async def test_customer_sees_only_their_tenant(env):
    app, Session = env
    ids = await _seed(Session)
    client, _ = await _client(app, ids["u2"])
    try:
        media = await client.get("/panel/media")
        assert media.status_code == 200
        assert "MEDIA2" in media.text and "MEDIA3" not in media.text
        users = await client.get("/panel/users")
        assert "2222" in users.text and "3333" not in users.text
    finally:
        await client.aclose()

    client3, _ = await _client(app, ids["u3"])
    try:
        media = await client3.get("/panel/media")
        assert "MEDIA3" in media.text and "MEDIA2" not in media.text
    finally:
        await client3.aclose()


async def test_cross_tenant_row_is_not_reachable(env):
    app, Session = env
    ids = await _seed(Session)
    client, _ = await _client(app, ids["u2"])
    try:
        # customer 2 crafts a request for customer 3's media id -> invisible
        resp = await client.get(f"/panel/media/{ids['m3']}", follow_redirects=False)
        assert resp.status_code in (302, 404)
        assert "MEDIA3" not in resp.text
        # their own row IS reachable
        own = await client.get(f"/panel/media/{ids['m2']}")
        assert own.status_code == 200 and "MEDIA2" in own.text
    finally:
        await client.aclose()


async def test_audit_records_acting_tenant(env):
    app, Session = env
    ids = await _seed(Session)
    client, csrf = await _client(app, ids["u2"])
    try:
        resp = await client.post(
            f"/panel/media/{ids['m2']}/toggle",
            data={"field": "is_active", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
    finally:
        await client.aclose()
    with all_tenants():
        async with Session() as s:
            row = await s.scalar(
                select(PanelAudit).where(PanelAudit.action == "media_toggle")
            )
    assert row is not None and row.tenant_id == ids["t2"]


async def test_unauthenticated_still_redirects(env):
    app, _ = env
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/panel/media", follow_redirects=False)
    assert resp.status_code == 302 and "/panel/login" in resp.headers["location"]
