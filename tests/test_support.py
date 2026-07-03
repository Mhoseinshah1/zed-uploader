"""H2 — in-bot support / ticketing.

Covers the service state machine, tenant isolation, the bot open-ticket flow,
and the panel surfaces: a tenant admin answers only their OWN users' tickets;
the super-admin platform inbox holds reseller->platform tickets across tenants;
a customer cannot read the platform inbox; tenant A's tickets are invisible to
tenant B.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.bot.handlers.support as support
from app.bot import messages
from app.core.redis_client import get_redis
from app.core.tenant_context import all_tenants, tenant_scope
from app.db.session import get_session
from app.models import Base, PanelUser, SupportTicket, Tenant, TicketMessage, User
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore
from app.services.support_service import SupportService

T_A = 2  # a customer/reseller tenant
T_B = 3  # a different customer tenant


# --------------------------------------------------------------------------- #
#  Harness
# --------------------------------------------------------------------------- #
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


async def _seed(Session):
    """Two customer tenants + a super-admin + a tenant-A admin + users."""
    with all_tenants():
        async with Session() as s:
            s.add_all([
                Tenant(id=T_A, bot_username="a", bot_id=2002, status="active"),
                Tenant(id=T_B, bot_username="b", bot_id=3003, status="active"),
            ])
            await s.commit()
            root = PanelUser(username="root", password_hash=hash_password("pw"),
                             tenant_id=1, is_superadmin=True)
            cust = PanelUser(username="cust", password_hash=hash_password("pw"),
                             tenant_id=T_A, is_superadmin=False)
            s.add_all([root, cust])
            await s.commit()
            ids = {"root": root.id, "cust": cust.id}
    with tenant_scope(T_A):
        async with Session() as s:
            ua = User(telegram_id=5001)
            s.add(ua)
            await s.commit()
            ids["user_a"] = ua.id
    with tenant_scope(T_B):
        async with Session() as s:
            ub = User(telegram_id=6001)
            s.add(ub)
            await s.commit()
            ids["user_b"] = ub.id
    return ids


async def _client(app, uid):
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    return client, csrf


# --------------------------------------------------------------------------- #
#  Service state machine + isolation
# --------------------------------------------------------------------------- #
async def test_open_add_close_state_machine(env):
    app, Session = env
    ids = await _seed(Session)
    with tenant_scope(T_A):
        async with Session() as s:
            svc = SupportService(s)
            t = await svc.open_ticket(ids["user_a"], "موضوع", "سلام", "tenant_admin")
            assert t.status == "open"
            assert (await svc.active_ticket_for(ids["user_a"], "tenant_admin")).id == t.id
            # admin reply -> answered
            t2, _ = await svc.add_message(t.id, "admin", "بله؟")
            assert t2.status == "answered"
            # user reply -> reopened
            t3, _ = await svc.add_message(t.id, "user", "ممنون")
            assert t3.status == "open"
            assert len(await svc.messages(t.id)) == 3
            assert await svc.close_ticket(t.id) is True
            assert (await svc.get(t.id)).status == "closed"


async def test_tickets_isolated_across_tenants(env):
    app, Session = env
    ids = await _seed(Session)
    with tenant_scope(T_A):
        async with Session() as s:
            await SupportService(s).open_ticket(ids["user_a"], "A", "a", "tenant_admin")
    with tenant_scope(T_B):
        async with Session() as s:
            await SupportService(s).open_ticket(ids["user_b"], "B", "b", "tenant_admin")
    # tenant A sees only its own ticket
    with tenant_scope(T_A):
        async with Session() as s:
            rows = await SupportService(s).list_by_target("tenant_admin")
            assert [r.subject for r in rows] == ["A"]
    with tenant_scope(T_B):
        async with Session() as s:
            rows = await SupportService(s).list_by_target("tenant_admin")
            assert [r.subject for r in rows] == ["B"]


async def test_platform_target_excluded_from_tenant_admin_list(env):
    app, Session = env
    ids = await _seed(Session)
    with tenant_scope(T_A):
        async with Session() as s:
            await SupportService(s).open_ticket(ids["user_a"], "toadmin", "x", "tenant_admin")
            await SupportService(s).open_ticket(ids["user_a"], "toplatform", "y", "platform")
        async with Session() as s:
            admin_list = await SupportService(s).list_by_target("tenant_admin")
            plat_list = await SupportService(s).list_by_target("platform")
    assert [t.subject for t in admin_list] == ["toadmin"]
    assert [t.subject for t in plat_list] == ["toplatform"]


# --------------------------------------------------------------------------- #
#  Bot open-ticket flow
# --------------------------------------------------------------------------- #
class _FakeState:
    def __init__(self):
        self._data, self._state = {}, None

    async def clear(self):
        self._data, self._state = {}, None

    async def set_state(self, s):
        self._state = s

    async def update_data(self, **kw):
        self._data.update(kw)

    async def get_data(self):
        return dict(self._data)


def _fake_message(text=""):
    return SimpleNamespace(
        text=text, answer=AsyncMock(),
        from_user=SimpleNamespace(id=5001),
        bot=SimpleNamespace(send_message=AsyncMock()),
    )


async def test_bot_user_opens_ticket_and_admin_notified(env, monkeypatch):
    app, Session = env
    ids = await _seed(Session)
    notified = {"n": 0}

    async def _notify(bot, session, text):
        notified["n"] += 1

    monkeypatch.setattr(support, "notify_tenant_admins", _notify)

    state = _FakeState()
    with tenant_scope(T_A):
        async with Session() as s:
            db_user = await s.get(User, ids["user_a"])
            # press "🎧 پشتیبانی" -> regular user -> tenant_admin, asks subject
            m1 = _fake_message()
            await support.support_menu(m1, state, s, db_user)
            assert (await state.get_data())["target"] == "tenant_admin"
            # subject
            m2 = _fake_message(text="نمی‌تونم وارد شوم")
            await support.support_subject(m2, state)
            # message body -> ticket created + admins notified
            m3 = _fake_message(text="خطای ورود دارم")
            await support.support_message(m3, state, s, db_user)
    assert notified["n"] == 1
    with tenant_scope(T_A):
        async with Session() as s:
            rows = await SupportService(s).list_by_target("tenant_admin")
            assert len(rows) == 1 and rows[0].subject == "نمی‌تونم وارد شوم"
            assert len(await SupportService(s).messages(rows[0].id)) == 1


# --------------------------------------------------------------------------- #
#  Panel surfaces
# --------------------------------------------------------------------------- #
async def test_tenant_admin_sees_and_answers_own_tickets(env):
    app, Session = env
    ids = await _seed(Session)
    with tenant_scope(T_A):
        async with Session() as s:
            t = await SupportService(s).open_ticket(ids["user_a"], "کمک", "سلام", "tenant_admin")
            tid = t.id
    client, csrf = await _client(app, ids["cust"])
    try:
        lst = await client.get("/panel/tickets")
        assert lst.status_code == 200 and "کمک" in lst.text
        detail = await client.get(f"/panel/tickets/{tid}")
        assert detail.status_code == 200
        rep = await client.post(
            f"/panel/tickets/{tid}/reply",
            data={"body": "بررسی می‌کنیم", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert rep.status_code == 302
    finally:
        await client.aclose()
    with tenant_scope(T_A):
        async with Session() as s:
            svc = SupportService(s)
            assert (await svc.get(tid)).status == "answered"
            msgs = await svc.messages(tid)
            assert msgs[-1].sender_kind == "admin" and msgs[-1].body == "بررسی می‌کنیم"


async def test_platform_ticket_only_in_superadmin_inbox(env):
    app, Session = env
    ids = await _seed(Session)
    with tenant_scope(T_A):
        async with Session() as s:
            t = await SupportService(s).open_ticket(ids["user_a"], "به پلتفرم", "درخواست", "platform")
            tid = t.id

    # the tenant admin does NOT see the platform ticket in their own list
    cust_client, _ = await _client(app, ids["cust"])
    try:
        lst = await cust_client.get("/panel/tickets")
        assert lst.status_code == 200 and "به پلتفرم" not in lst.text
        # and cannot open the platform inbox at all
        assert (await cust_client.get("/panel/platform/support")).status_code == 403
        # nor the platform ticket detail
        assert (await cust_client.get(f"/panel/tickets/{tid}")).status_code == 302
    finally:
        await cust_client.aclose()

    # the super-admin sees it in the platform inbox and can reply
    root_client, csrf = await _client(app, ids["root"])
    try:
        inbox = await root_client.get("/panel/platform/support")
        assert inbox.status_code == 200 and "به پلتفرم" in inbox.text
        rep = await root_client.post(
            f"/panel/platform/support/{tid}/reply",
            data={"body": "پاسخ پلتفرم", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert rep.status_code == 302
    finally:
        await root_client.aclose()
    with all_tenants():
        async with Session() as s:
            assert (await s.get(SupportTicket, tid)).status == "answered"


async def test_tenant_cannot_open_another_tenants_ticket(env):
    app, Session = env
    ids = await _seed(Session)
    with tenant_scope(T_B):
        async with Session() as s:
            t = await SupportService(s).open_ticket(ids["user_b"], "مال B", "x", "tenant_admin")
            other_id = t.id
    client, _ = await _client(app, ids["cust"])  # cust is tenant A
    try:
        # tenant A admin cannot view tenant B's ticket -> redirected, never 200
        resp = await client.get(f"/panel/tickets/{other_id}", follow_redirects=False)
        assert resp.status_code == 302
        lst = await client.get("/panel/tickets")
        assert "مال B" not in lst.text
    finally:
        await client.aclose()
