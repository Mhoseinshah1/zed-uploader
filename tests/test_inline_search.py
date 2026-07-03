"""J2 — inline search: approved-only, tenant-scoped, escaped, paginated, gated."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.bot.handlers.inline as inline_h
from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Base, Media, Tenant, User
from app.services.bot_setting_service import KEY_PUBLIC_SEARCH_ENABLED, BotSettingService

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
                Tenant(id=T_A, bot_username="abot", bot_id=2002, status="active"),
                Tenant(id=T_B, bot_username="bbot", bot_id=3003, status="active"),
            ])
            await s.commit()
    try:
        yield Session
    finally:
        await engine.dispose()


def _query(text, offset="", tg=5001):
    return SimpleNamespace(
        query=text, offset=offset,
        from_user=SimpleNamespace(id=tg),
        answer=AsyncMock(),
    )


async def _seed(sm):
    with tenant_scope(T_A):
        async with sm() as s:
            await BotSettingService(s).set(KEY_PUBLIC_SEARCH_ENABLED, True)
            u = User(telegram_id=5001)
            s.add(u)
            s.add_all([
                Media(code="FILM1", title="فیلم اول", status="approved"),
                Media(code="FILM2", title="فیلم دوم", status="approved"),
                Media(code="SECRET", title="فیلم مخفی", status="pending"),
                Media(code="DEAD", title="فیلم غیرفعال", status="approved", is_active=False),
            ])
            await s.commit()
            return u


async def test_inline_returns_only_approved_active(sm):
    user = await _seed(sm)
    q = _query("فیلم")
    with tenant_scope(T_A):
        async with sm() as s:
            await inline_h.inline_search(q, s, user)
    results = q.answer.await_args.args[0]
    titles = [r.title for r in results]
    assert "فیلم اول" in titles and "فیلم دوم" in titles
    assert "فیلم مخفی" not in titles and "فیلم غیرفعال" not in titles
    # deep links point at THIS tenant's bot
    for r in results:
        assert "t.me/abot?start=" in r.input_message_content.message_text


async def test_inline_no_cross_tenant_leak(sm):
    await _seed(sm)
    with tenant_scope(T_B):
        async with sm() as s:
            await BotSettingService(s).set(KEY_PUBLIC_SEARCH_ENABLED, True)
            ub = User(telegram_id=6001)
            s.add(ub)
            s.add(Media(code="BFILE", title="فیلم بی", status="approved"))
            await s.commit()
    q = _query("فیلم", tg=6001)
    with tenant_scope(T_B):
        async with sm() as s:
            ub = await s.merge(ub)
            await inline_h.inline_search(q, s, ub)
    titles = [r.title for r in q.answer.await_args.args[0]]
    assert titles == ["فیلم بی"]  # never tenant A's files


async def test_inline_gated_when_public_search_off(sm):
    user = await _seed(sm)
    with tenant_scope(T_A):
        async with sm() as s:
            await BotSettingService(s).set(KEY_PUBLIC_SEARCH_ENABLED, False)
        async with sm() as s:
            q = _query("فیلم")
            await inline_h.inline_search(q, s, user)
    assert q.answer.await_args.args[0] == []  # gated -> no results


async def test_inline_escaping_no_wildcard_scan(sm):
    user = await _seed(sm)
    q = _query("%")  # a bare LIKE wildcard must be treated literally
    with tenant_scope(T_A):
        async with sm() as s:
            await inline_h.inline_search(q, s, user)
    assert q.answer.await_args.args[0] == []  # matches nothing, not everything


async def test_inline_pagination_next_offset(sm, monkeypatch):
    monkeypatch.setattr(inline_h, "PAGE", 1)  # force paging with tiny page
    user = await _seed(sm)
    q = _query("فیلم")
    with tenant_scope(T_A):
        async with sm() as s:
            await inline_h.inline_search(q, s, user)
    kwargs = q.answer.await_args.kwargs
    assert kwargs["next_offset"] == "1"  # more results remain
    q2 = _query("فیلم", offset="1")
    with tenant_scope(T_A):
        async with sm() as s:
            await inline_h.inline_search(q2, s, user)
    assert len(q2.answer.await_args.args[0]) == 1  # the second page
