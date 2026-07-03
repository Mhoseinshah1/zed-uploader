"""I2 — panel roles: each role allowed/denied on the right routes.

owner/finance -> wallet adjust + payments; owner/admin/content -> media/review;
owner-only -> settings/providers/plans/team; users VIEW -> owner/admin/support/
finance (but wallet adjust within stays owner/finance). Platform super-admin
bypasses tenant roles.
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
from app.models import Base, PanelUser, Tenant, User
from app.models.panel import PANEL_ROLES
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore

T = 2
ROLES = list(PANEL_ROLES)  # owner, admin, support, finance, content


@pytest_asyncio.fixture
async def env():
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
            for r in ROLES:
                pu = PanelUser(username=f"u_{r}", password_hash=hash_password("pw"),
                               tenant_id=T, role=r, is_superadmin=False)
                s.add(pu)
                await s.flush()
                ids[r] = pu.id
            sup = PanelUser(username="root", password_hash=hash_password("pw"),
                            tenant_id=1, role="owner", is_superadmin=True)
            s.add(sup)
            await s.commit()
            ids["super"] = sup.id
    with tenant_scope(T):
        async with Session() as s:
            u = User(telegram_id=5001)
            s.add(u)
            await s.commit()
            ids["target_user"] = u.id
    from app.api.main import app

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield app, ids
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def _client(app, uid):
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    return client, csrf


GET_ROUTES = {
    "/panel/settings": {"owner"},
    "/panel/providers": {"owner"},
    "/panel/plans": {"owner"},
    "/panel/team": {"owner"},
    "/panel/payments": {"owner", "finance"},
    "/panel/users": {"owner", "admin", "support", "finance"},
    "/panel/media": {"owner", "admin", "content"},
    "/panel/review": {"owner", "admin", "content"},
}


async def test_get_route_role_matrix(env):
    app, ids = env
    for role in ROLES:
        client, _ = await _client(app, ids[role])
        try:
            for route, allowed in GET_ROUTES.items():
                resp = await client.get(route)
                expected = 200 if role in allowed else 403
                assert resp.status_code == expected, (route, role, resp.status_code)
        finally:
            await client.aclose()


async def test_superadmin_bypasses_tenant_roles(env):
    app, ids = env
    client, _ = await _client(app, ids["super"])
    try:
        for route in GET_ROUTES:
            assert (await client.get(route)).status_code == 200, route
    finally:
        await client.aclose()


async def test_wallet_adjust_is_finance_only(env):
    app, ids = env
    uid = ids["target_user"]
    for role in ROLES:
        client, csrf = await _client(app, ids[role])
        try:
            resp = await client.post(
                f"/panel/users/{uid}/wallet",
                data={"direction": "credit", "amount": "100", "reason": "t", "csrf_token": csrf},
                follow_redirects=False,
            )
            if role in {"owner", "finance"}:
                assert resp.status_code == 302, role  # performed
            else:
                assert resp.status_code == 403, role  # support/admin/content denied
        finally:
            await client.aclose()


async def test_support_can_view_users_but_not_adjust(env):
    app, ids = env
    client, csrf = await _client(app, ids["support"])
    try:
        assert (await client.get("/panel/users")).status_code == 200  # can view
        r = await client.post(
            f"/panel/users/{ids['target_user']}/wallet",
            data={"direction": "credit", "amount": "50", "reason": "t", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert r.status_code == 403  # cannot adjust wallets
    finally:
        await client.aclose()


async def test_owner_manages_team_roles(env):
    app, ids = env
    owner_client, ocsrf = await _client(app, ids["owner"])
    try:
        # promote the support user to finance
        r = await owner_client.post(
            f"/panel/team/{ids['support']}/role",
            data={"role": "finance", "csrf_token": ocsrf}, follow_redirects=False,
        )
        assert r.status_code == 302
        # cannot escalate anyone to super-admin via the role field (invalid -> no-op)
        await owner_client.post(
            f"/panel/team/{ids['admin']}/role",
            data={"role": "superadmin", "csrf_token": ocsrf}, follow_redirects=False,
        )
    finally:
        await owner_client.aclose()

    # the promotion took effect: the (now finance) user reaches a finance route
    promoted, _ = await _client(app, ids["support"])
    try:
        assert (await promoted.get("/panel/payments")).status_code == 200
    finally:
        await promoted.aclose()

    # the invalid super-admin escalation did NOT happen: admin still can't
    # reach an owner-only route
    still_admin, _ = await _client(app, ids["admin"])
    try:
        assert (await still_admin.get("/panel/settings")).status_code == 403
        assert (await still_admin.get("/panel/team")).status_code == 403  # non-owner
    finally:
        await still_admin.aclose()
