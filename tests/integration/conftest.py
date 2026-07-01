"""Integration fixtures — a REAL Postgres engine gated on TEST_DATABASE_URL.

SQLite is intentionally NOT accepted here: the money-safety tests rely on
Postgres semantics (SELECT ... FOR UPDATE row locks, RETURNING) that SQLite
does not reproduce.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

requires_pg = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason=(
        "money integration tests need a real Postgres — set "
        "TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/zed_test"
    ),
)


@pytest_asyncio.fixture
async def pg_sessionmaker():
    """Fresh schema per test on the real Postgres test DB."""
    engine = create_async_engine(
        TEST_DATABASE_URL, pool_size=20, max_overflow=20, pool_pre_ping=True
    )
    async with engine.begin() as conn:
        # pg_trgm is needed for the media trigram indexes create_all emits (B3).
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()
