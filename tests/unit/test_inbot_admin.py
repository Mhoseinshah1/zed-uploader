"""G3 — in-bot admin extras: log-group setup + panel deep-link buttons.

The in-bot log-group setup calls the SAME TenantLogger the panel uses (tenant
scoped), and the panel-links handler emits only URL buttons — no secret text.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot.handlers.admin_panel import log_group_input, panel_links
from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Base, PanelUser, Tenant
from app.services.tenant_logger import TenantLogger


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


def _msg(text=""):
    return SimpleNamespace(
        text=text, from_user=SimpleNamespace(id=1), answer=AsyncMock()
    )


async def _tenant(maker) -> int:
    with all_tenants():
        async with maker() as s:
            t = Tenant(bot_username="c", status="active")
            s.add(t)
            await s.commit()
            return t.id


async def test_inbot_log_group_setup_uses_tenant_logger(maker):
    tid = await _tenant(maker)
    state = SimpleNamespace(clear=AsyncMock())
    msg = _msg("-1009999")
    with tenant_scope(tid):
        async with maker() as s:
            await log_group_input(msg, state, s)
        # stored via the SAME service the panel uses, scoped to this tenant
        async with maker() as s:
            row = await TenantLogger(s).get_settings()
    assert row is not None and row.log_group_id == -1009999
    msg.answer.assert_awaited()


async def test_inbot_log_group_invalid_is_rejected(maker):
    tid = await _tenant(maker)
    state = SimpleNamespace(clear=AsyncMock())
    msg = _msg("not-a-number")
    with tenant_scope(tid):
        async with maker() as s:
            await log_group_input(msg, state, s)
        async with maker() as s:
            row = await TenantLogger(s).get_settings()
    assert row is None  # nothing stored


async def test_panel_links_emits_url_buttons_no_secret(maker):
    tid = await _tenant(maker)
    with all_tenants():
        async with maker() as s:
            s.add(PanelUser(username="bot9", password_hash="SECRETHASH", tenant_id=tid))
            await s.commit()
    state = SimpleNamespace(clear=AsyncMock())
    msg = _msg()
    with tenant_scope(tid):
        async with maker() as s:
            await panel_links(msg, state, s)
    kwargs = msg.answer.await_args.kwargs
    markup = kwargs["reply_markup"]
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert buttons and all(b.url and "/panel/link/" in b.url for b in buttons)
    # the message text carries NO secret (password hash etc.)
    text = msg.answer.await_args.args[0]
    assert "SECRETHASH" not in text


async def test_panel_links_no_account(maker):
    tid = await _tenant(maker)  # no panel user for this tenant
    state = SimpleNamespace(clear=AsyncMock())
    msg = _msg()
    with tenant_scope(tid):
        async with maker() as s:
            await panel_links(msg, state, s)
    # a plain message (no inline keyboard) — cannot mint a link
    assert msg.answer.await_args.kwargs.get("reply_markup") is None
