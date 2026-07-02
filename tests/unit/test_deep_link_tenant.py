"""Fix-1 — MediaService.deep_link uses the CURRENT tenant's bot username.

A customer tenant's file links must point at that tenant's bot, never the
platform bot (settings.bot_username). The platform tenant uses the env username.
"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Base, Media, Tenant
from app.services.media_service import MediaService


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


async def test_customer_deep_link_uses_own_bot(maker):
    with all_tenants():
        async with maker() as s:
            t = Tenant(bot_username="customer_bot", bot_id=42, status="active")
            s.add(t)
            await s.commit()
            tid = t.id
    with tenant_scope(tid):
        async with maker() as s:
            s.add(Media(code="XYZ", status="approved"))
            await s.commit()
            media = (await MediaService(s).get_by_code("XYZ"))
            link = await MediaService(s).deep_link(media)
    assert link == "https://t.me/customer_bot?start=XYZ"
    assert settings.bot_username not in link  # not the platform bot


async def test_platform_deep_link_uses_env_username(maker):
    # platform tenant (id 1) has the placeholder 'platform' username in its row;
    # the deep link must use the real env BOT_USERNAME instead.
    with tenant_scope(1):
        async with maker() as s:
            s.add(Media(code="PLAT", status="approved"))
            await s.commit()
            media = await MediaService(s).get_by_code("PLAT")
            link = await MediaService(s).deep_link(media)
    assert link == f"https://t.me/{settings.bot_username}?start=PLAT"
    assert "/platform?" not in link  # not the seeded placeholder username
