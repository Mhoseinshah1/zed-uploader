"""B1 unit tests — user-upload settings defaults + create_media status default.

In-memory SQLite (no locks here), so SQLite is acceptable.
"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base, User
from app.services.bot_setting_service import BotSettingService
from app.services.media_service import MediaService


@pytest_asyncio.fixture
async def sqlite_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_user_upload_defaults(sqlite_maker):
    async with sqlite_maker() as s:
        svc = BotSettingService(s)
        # defaults: uploads OFF, review ON
        assert await svc.user_upload_enabled() is False
        assert await svc.user_upload_requires_review() is True


async def test_user_upload_toggle(sqlite_maker):
    async with sqlite_maker() as s:
        svc = BotSettingService(s)
        await svc.set("user_upload_enabled", True)
        await svc.set("user_upload_requires_review", False)
        assert await svc.user_upload_enabled() is True
        assert await svc.user_upload_requires_review() is False


async def test_create_media_defaults_to_approved(sqlite_maker):
    async with sqlite_maker() as s:
        user = User(telegram_id=555)
        s.add(user)
        await s.commit()
        media = await MediaService(s).create_media(
            files=[{"telegram_file_id": "f", "file_type": "document"}],
            owner_user_id=user.id,
        )
        assert media.status == "approved"  # admin/default uploads stay live

        pending = await MediaService(s).create_media(
            files=[{"telegram_file_id": "f2", "file_type": "document"}],
            owner_user_id=user.id,
            status="pending",
        )
        assert pending.status == "pending"
