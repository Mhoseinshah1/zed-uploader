"""I1 — real user blocking (security).

The BlockedUserMiddleware stops ALL handler processing for a blocked, non-admin
user (message / callback / pre_checkout) and replies once; admins/owners bypass.
Defense-in-depth: deliver_by_code refuses a blocked user even via a deep link,
and the broadcast snapshot excludes blocked users.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram.types import User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.bot.middlewares.blocked as blocked_mod
from app.bot import messages
from app.bot.delivery import DeliveryStatus, deliver_by_code
from app.bot.middlewares.blocked import BlockedUserMiddleware
from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Admin, Base, BroadcastRecipient, Tenant, User
from app.services import broadcast as bcast

T = 2


class _Handler:
    def __init__(self):
        self.called = False

    async def __call__(self, event, data):
        self.called = True
        return "ran"


def _user(is_blocked, tg=999):
    return SimpleNamespace(is_blocked=is_blocked, telegram_id=tg)


def _patch_is_admin(monkeypatch, value: bool):
    async def _stub(session, telegram_id):
        return value

    monkeypatch.setattr(blocked_mod.AdminService, "is_admin", _stub)


# --- middleware: message / callback / pre_checkout stopped for blocked ------
async def test_blocked_message_is_stopped(monkeypatch):
    _patch_is_admin(monkeypatch, False)
    mw, h = BlockedUserMiddleware(), _Handler()
    msg = SimpleNamespace(answer=AsyncMock())
    res = await mw(h, SimpleNamespace(message=msg), {"db_user": _user(True), "session": object()})
    assert h.called is False and res is None
    msg.answer.assert_awaited_once_with(messages.ACCOUNT_BLOCKED)


async def test_blocked_callback_is_stopped(monkeypatch):
    _patch_is_admin(monkeypatch, False)
    mw, h = BlockedUserMiddleware(), _Handler()
    cb = SimpleNamespace(answer=AsyncMock())
    await mw(h, SimpleNamespace(callback_query=cb), {"db_user": _user(True), "session": object()})
    assert h.called is False
    cb.answer.assert_awaited_once_with(messages.ACCOUNT_BLOCKED, show_alert=True)


async def test_blocked_pre_checkout_is_refused(monkeypatch):
    """A blocked user's Stars purchase can never complete."""
    _patch_is_admin(monkeypatch, False)
    mw, h = BlockedUserMiddleware(), _Handler()
    pcq = SimpleNamespace(answer=AsyncMock())
    await mw(h, SimpleNamespace(pre_checkout_query=pcq), {"db_user": _user(True), "session": object()})
    assert h.called is False
    pcq.answer.assert_awaited_once_with(ok=False, error_message=messages.ACCOUNT_BLOCKED)


async def test_admin_owner_bypasses_block(monkeypatch):
    _patch_is_admin(monkeypatch, True)  # a blocked user who is an admin/owner
    mw, h = BlockedUserMiddleware(), _Handler()
    msg = SimpleNamespace(answer=AsyncMock())
    await mw(h, SimpleNamespace(message=msg), {"db_user": _user(True), "session": object()})
    assert h.called is True  # processing proceeds
    msg.answer.assert_not_awaited()


async def test_active_user_passes_through(monkeypatch):
    _patch_is_admin(monkeypatch, False)
    mw, h = BlockedUserMiddleware(), _Handler()
    msg = SimpleNamespace(answer=AsyncMock())
    await mw(h, SimpleNamespace(message=msg), {"db_user": _user(False), "session": object()})
    assert h.called is True
    msg.answer.assert_not_awaited()


# --- delivery + broadcast (real DB) ----------------------------------------
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


async def test_deliver_by_code_refuses_blocked_deep_link(sm):
    with tenant_scope(T):
        async with sm() as s:
            s.add(User(telegram_id=777, is_blocked=True))
            await s.commit()
    with tenant_scope(T):
        async with sm() as s:
            res = await deliver_by_code(
                object(), s, chat_id=777,
                user=TgUser(id=777, is_bot=False, first_name="x"), code="ANY",
            )
    assert res.status is DeliveryStatus.BLOCKED  # refused even via deep link


async def test_deliver_by_code_admin_bypass(sm):
    with tenant_scope(T):
        async with sm() as s:
            s.add(User(telegram_id=888, is_blocked=True))
            s.add(Admin(telegram_id=888, role="owner", is_active=True))
            await s.commit()
    with tenant_scope(T):
        async with sm() as s:
            res = await deliver_by_code(
                object(), s, chat_id=888,
                user=TgUser(id=888, is_bot=False, first_name="x"), code="NOPE",
            )
    # a blocked owner is NOT blocked here (proceeds -> NOT_FOUND, no media)
    assert res.status is DeliveryStatus.NOT_FOUND


async def test_broadcast_snapshot_excludes_blocked(sm):
    with tenant_scope(T):
        async with sm() as s:
            s.add_all([
                User(telegram_id=1, is_blocked=False),
                User(telegram_id=2, is_blocked=True),
                User(telegram_id=3, is_blocked=False),
            ])
            await s.commit()
        async with sm() as s:
            assert await bcast.audience_count(s) == 2  # blocked not counted
            job = await bcast.create_job(s, text="hi")
        async with sm() as s:
            tgs = set(
                (await s.scalars(
                    select(BroadcastRecipient.telegram_id).where(
                        BroadcastRecipient.broadcast_id == job.id
                    )
                )).all()
            )
    assert tgs == {1, 3}  # the blocked user (2) is excluded from the snapshot
