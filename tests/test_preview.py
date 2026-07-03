"""J5 — channel preview auto-post: deep link, toggle, idempotency, resilience."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Base, Media, MediaPreview, Tenant
from app.services.bot_setting_service import (
    KEY_PREVIEW_CHANNEL_ID,
    KEY_PREVIEW_ENABLED,
    BotSettingService,
)
from app.services.preview_service import maybe_post_preview

T = 2
CHANNEL = -1001234567890


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
            s.add(Tenant(id=T, bot_username="acmebot", bot_id=2002, status="active"))
            await s.commit()
    try:
        yield Session
    finally:
        await engine.dispose()


def _bot():
    return SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=44)),
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id=45)),
    )


async def _enable(sm):
    with tenant_scope(T):
        async with sm() as s:
            st = BotSettingService(s)
            await st.set(KEY_PREVIEW_ENABLED, True)
            await st.set(KEY_PREVIEW_CHANNEL_ID, str(CHANNEL))


async def _media(sm, **kw):
    with tenant_scope(T):
        async with sm() as s:
            m = Media(code="PV1", status="approved", title="فیلم", **kw)
            s.add(m)
            await s.commit()
            return m


async def test_preview_posted_with_tenant_deep_link(sm):
    await _enable(sm)
    m = await _media(sm)
    bot = _bot()
    with tenant_scope(T):
        async with sm() as s:
            m = await s.merge(m)
            assert await maybe_post_preview(s, m, bot=bot) is True
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == CHANNEL
    url = kwargs["reply_markup"].inline_keyboard[0][0].url
    assert url == "https://t.me/acmebot?start=PV1"  # THIS tenant's bot
    with tenant_scope(T):
        async with sm() as s:
            row = await s.scalar(select(MediaPreview))
            assert row.media_id == m.id and row.message_id == 44


async def test_preview_uses_thumbnail_photo_when_set(sm):
    await _enable(sm)
    m = await _media(sm, thumbnail_file_id="PHOTO9")
    bot = _bot()
    with tenant_scope(T):
        async with sm() as s:
            m = await s.merge(m)
            assert await maybe_post_preview(s, m, bot=bot) is True
    assert bot.send_photo.await_args.kwargs["photo"] == "PHOTO9"
    bot.send_message.assert_not_awaited()


async def test_preview_disabled_no_post(sm):
    m = await _media(sm)  # preview never enabled
    bot = _bot()
    with tenant_scope(T):
        async with sm() as s:
            m = await s.merge(m)
            assert await maybe_post_preview(s, m, bot=bot) is False
    bot.send_message.assert_not_awaited()


async def test_preview_idempotent_once_per_media(sm):
    await _enable(sm)
    m = await _media(sm)
    bot = _bot()
    with tenant_scope(T):
        async with sm() as s:
            m = await s.merge(m)
            assert await maybe_post_preview(s, m, bot=bot) is True
        async with sm() as s:
            m2 = await s.get(Media, m.id)
            assert await maybe_post_preview(s, m2, bot=bot) is False  # already posted
        async with sm() as s:
            n = int(await s.scalar(select(func.count(MediaPreview.id))))
    assert n == 1 and bot.send_message.await_count == 1


async def test_channel_failure_never_breaks_caller(sm):
    await _enable(sm)
    m = await _media(sm)
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=RuntimeError("bot is not channel admin")),
        send_photo=AsyncMock(),
    )
    with tenant_scope(T):
        async with sm() as s:
            m = await s.merge(m)
            assert await maybe_post_preview(s, m, bot=bot) is False  # swallowed
        async with sm() as s:
            n = int(await s.scalar(select(func.count(MediaPreview.id))))
    assert n == 0  # nothing recorded on failure -> a later retry may post
