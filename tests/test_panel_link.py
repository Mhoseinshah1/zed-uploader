"""G3 — secure in-bot panel deep-link: single-use, expiring, tenant-scoped.

Exercises PanelLinkService + the /panel/link/{token} route end-to-end via the
real app, proving a consumed link opens ONLY the bound tenant's panel and a
replay / cross-tenant / off-site target is rejected.
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
from app.models import Base, Media, PanelUser, Tenant
from app.panel.link_service import PanelLinkService
from app.panel.session import COOKIE_NAME


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
    with all_tenants():
        async with Session() as s:
            t2, t3 = Tenant(bot_username="c2", status="active"), Tenant(bot_username="c3", status="active")
            s.add_all([t2, t3])
            await s.commit()
            t2id, t3id = t2.id, t3.id
            pu2 = PanelUser(username="c2", password_hash="x", tenant_id=t2id)
            pu3 = PanelUser(username="c3", password_hash="x", tenant_id=t3id)
            s.add_all([pu2, pu3])
            await s.commit()
            ids = {"t2": t2id, "t3": t3id, "u2": pu2.id, "u3": pu3.id}
    with tenant_scope(t2id):
        async with Session() as s:
            s.add(Media(code="MEDIA2", status="approved"))
            await s.commit()
    with tenant_scope(t3id):
        async with Session() as s:
            s.add(Media(code="MEDIA3", status="approved"))
            await s.commit()
    return ids


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_link_opens_only_bound_tenant(env):
    app, Session = env
    ids = await _seed(Session)
    token = await PanelLinkService(get_redis()).mint(
        tenant_id=ids["t2"], panel_user_id=ids["u2"], target="/panel/media"
    )
    async with _client(app) as c:
        resp = await c.get(f"/panel/link/{token}", follow_redirects=False)
        assert resp.status_code == 302 and resp.headers["location"] == "/panel/media"
        assert COOKIE_NAME in resp.cookies
        c.cookies.set(COOKIE_NAME, resp.cookies[COOKIE_NAME])
        media = await c.get("/panel/media")
        assert "MEDIA2" in media.text and "MEDIA3" not in media.text  # tenant-scoped


async def test_link_is_single_use(env):
    app, Session = env
    ids = await _seed(Session)
    token = await PanelLinkService(get_redis()).mint(
        tenant_id=ids["t2"], panel_user_id=ids["u2"], target="/panel"
    )
    async with _client(app) as c:
        first = await c.get(f"/panel/link/{token}", follow_redirects=False)
        assert first.status_code == 302 and first.headers["location"] == "/panel"
        c.cookies.clear()
        second = await c.get(f"/panel/link/{token}", follow_redirects=False)
        assert "/panel/login" in second.headers["location"]  # already consumed


async def test_cross_tenant_token_rejected(env):
    app, Session = env
    ids = await _seed(Session)
    # token claims tenant 3 but binds tenant 2's user -> mismatch -> rejected
    token = await PanelLinkService(get_redis()).mint(
        tenant_id=ids["t3"], panel_user_id=ids["u2"], target="/panel"
    )
    async with _client(app) as c:
        resp = await c.get(f"/panel/link/{token}", follow_redirects=False)
        assert "/panel/login" in resp.headers["location"]
        assert COOKIE_NAME not in resp.cookies


async def test_offsite_target_is_sanitized(env):
    app, Session = env
    ids = await _seed(Session)
    token = await PanelLinkService(get_redis()).mint(
        tenant_id=ids["t2"], panel_user_id=ids["u2"], target="https://evil.example/x"
    )
    async with _client(app) as c:
        resp = await c.get(f"/panel/link/{token}", follow_redirects=False)
        assert resp.headers["location"] == "/panel"  # no open redirect


async def test_service_consume_is_atomic_single_use():
    svc = PanelLinkService(get_redis())
    token = await svc.mint(tenant_id=5, panel_user_id=9, target="/panel")
    first = await svc.consume(token)
    assert first == {"t": 5, "u": 9, "p": "/panel"}
    assert await svc.consume(token) is None  # gone after one use
