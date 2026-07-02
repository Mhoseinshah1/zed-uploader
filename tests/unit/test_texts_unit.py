"""D3 unit tests — text override/fallback, Redis cache bust, Redis-down grace."""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.redis_client as redis_client
from app.bot import messages
from app.models import Base
from app.services.text_service import get_text, set_text


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


async def test_fallback_to_default_when_unset(sqlite_maker):
    async with sqlite_maker() as s:
        assert await get_text(s, "welcome") == messages.WELCOME
        assert await get_text(s, "not_found") == messages.NOT_FOUND


async def test_override_applied_and_cache_busted_on_save(sqlite_maker):
    async with sqlite_maker() as s:
        await set_text(s, "welcome", "سلام سفارشی!")
        assert await get_text(s, "welcome") == "سلام سفارشی!"

        # the value is now cached; a save must bust it immediately (no TTL wait)
        assert await get_text(s, "welcome") == "سلام سفارشی!"  # cache hit
        await set_text(s, "welcome", "نسخهٔ دوم")
        assert await get_text(s, "welcome") == "نسخهٔ دوم"

        # clearing the override reverts to the built-in default
        await set_text(s, "welcome", "")
        assert await get_text(s, "welcome") == messages.WELCOME


async def test_unknown_key_is_ignored(sqlite_maker):
    async with sqlite_maker() as s:
        await set_text(s, "no_such_key", "x")  # no-op, no crash
        assert await get_text(s, "no_such_key") == ""


async def test_missing_redis_degrades_gracefully(sqlite_maker, monkeypatch):
    class _DeadRedis:
        def __getattr__(self, name):
            async def boom(*a, **kw):
                raise ConnectionError("redis down")

            return boom

    monkeypatch.setattr(redis_client, "_client", _DeadRedis())
    async with sqlite_maker() as s:
        # no crash; override still resolves straight from the DB
        await set_text(s, "help", "راهنمای سفارشی")
        assert await get_text(s, "help") == "راهنمای سفارشی"
        await set_text(s, "help", "")
        assert await get_text(s, "help") == messages.HELP
