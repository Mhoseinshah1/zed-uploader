"""J7 — maintenance mode: non-admin paused, admin bypass, per-tenant."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import messages
from app.bot.middlewares.maintenance import MaintenanceMiddleware
from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Admin, Base, Tenant
from app.services.bot_setting_service import (
    KEY_MAINTENANCE_MESSAGE,
    KEY_MAINTENANCE_MODE,
    BotSettingService,
)

T_A, T_B = 2, 3


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
            s.add_all([
                Tenant(id=T_A, bot_username="a", bot_id=2002, status="active"),
                Tenant(id=T_B, bot_username="b", bot_id=3003, status="active"),
            ])
            await s.commit()
    try:
        yield Session
    finally:
        await engine.dispose()


class _Handler:
    def __init__(self):
        self.called = False

    async def __call__(self, event, data):
        self.called = True
        return "ran"


def _event():
    return SimpleNamespace(message=SimpleNamespace(answer=AsyncMock()))


async def test_maintenance_blocks_non_admin_with_message(sm):
    with tenant_scope(T_A):
        async with sm() as s:
            st = BotSettingService(s)
            await st.set(KEY_MAINTENANCE_MODE, True)
            await st.set(KEY_MAINTENANCE_MESSAGE, "برمی‌گردیم!")
        async with sm() as s:
            mw, h, ev = MaintenanceMiddleware(), _Handler(), _event()
            out = await mw(
                h, ev,
                {"session": s, "event_from_user": SimpleNamespace(id=5111)},
            )
    assert h.called is False and out is None
    ev.message.answer.assert_awaited_once_with("برمی‌گردیم!")  # editable message


async def test_maintenance_admin_bypasses(sm):
    with tenant_scope(T_A):
        async with sm() as s:
            await BotSettingService(s).set(KEY_MAINTENANCE_MODE, True)
            s.add(Admin(telegram_id=5333, role="owner", is_active=True))
            await s.commit()
        async with sm() as s:
            mw, h, ev = MaintenanceMiddleware(), _Handler(), _event()
            await mw(h, ev, {"session": s, "event_from_user": SimpleNamespace(id=5333)})
    assert h.called is True
    ev.message.answer.assert_not_awaited()


async def test_maintenance_off_normal_and_default_message(sm):
    with tenant_scope(T_A):
        async with sm() as s:  # off by default
            mw, h = MaintenanceMiddleware(), _Handler()
            await mw(h, _event(), {"session": s, "event_from_user": SimpleNamespace(id=5111)})
            assert h.called is True
        async with sm() as s:  # on, no custom message -> the Persian default
            await BotSettingService(s).set(KEY_MAINTENANCE_MODE, True)
        async with sm() as s:
            mw, h, ev = MaintenanceMiddleware(), _Handler(), _event()
            await mw(h, ev, {"session": s, "event_from_user": SimpleNamespace(id=5111)})
    ev.message.answer.assert_awaited_once_with(messages.MAINTENANCE_DEFAULT)


async def test_maintenance_is_per_tenant(sm):
    with tenant_scope(T_A):
        async with sm() as s:
            await BotSettingService(s).set(KEY_MAINTENANCE_MODE, True)
    # tenant B is unaffected
    with tenant_scope(T_B):
        async with sm() as s:
            mw, h = MaintenanceMiddleware(), _Handler()
            await mw(h, _event(), {"session": s, "event_from_user": SimpleNamespace(id=5111)})
    assert h.called is True
