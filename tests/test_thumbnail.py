"""J4 — video thumbnail/cover: set/clear + delivery pass-through + fallback."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot.sender import send_media_file
from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Base, Media, MediaFile, Tenant, User
from app.services.media_service import MediaService

T = 2


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


async def test_set_and_clear_thumbnail(sm):
    with tenant_scope(T):
        async with sm() as s:
            owner = User(telegram_id=7001)
            s.add(owner)
            await s.flush()
            m = Media(code="V1", status="approved", owner_user_id=owner.id)
            s.add(m)
            await s.commit()
            svc = MediaService(s)
            assert await svc.set_thumbnail(m.id, owner.id, "PHOTO123") is True
        async with sm() as s:
            assert (await s.get(Media, m.id)).thumbnail_file_id == "PHOTO123"
            assert await MediaService(s).set_thumbnail(m.id, owner.id, None) is True
        async with sm() as s:
            assert (await s.get(Media, m.id)).thumbnail_file_id is None
        # a non-owner can't touch it
        async with sm() as s:
            assert await MediaService(s).set_thumbnail(m.id, 999999, "X") is False


def _video_file():
    return SimpleNamespace(file_type="video", telegram_file_id="VID1")


async def test_send_video_passes_thumbnail_when_present():
    bot = SimpleNamespace(send_video=AsyncMock(return_value=SimpleNamespace(message_id=5)))
    mid = await send_media_file(bot, 1, _video_file(), thumbnail="PHOTO123")
    assert mid == 5
    assert bot.send_video.await_args.kwargs["thumbnail"] == "PHOTO123"


async def test_send_video_absent_thumbnail_normal_send():
    bot = SimpleNamespace(send_video=AsyncMock(return_value=SimpleNamespace(message_id=6)))
    await send_media_file(bot, 1, _video_file())  # no thumbnail
    assert "thumbnail" not in bot.send_video.await_args.kwargs


async def test_send_video_falls_back_when_cover_rejected():
    """An API/library that rejects a reused file_id must never break delivery."""
    calls = {"n": 0}

    async def _send_video(**kw):
        calls["n"] += 1
        if "thumbnail" in kw:
            raise TypeError("thumbnail must be InputFile")
        return SimpleNamespace(message_id=7)

    bot = SimpleNamespace(send_video=_send_video)
    mid = await send_media_file(bot, 1, _video_file(), thumbnail="PHOTO123")
    assert mid == 7 and calls["n"] == 2  # retried without the cover
