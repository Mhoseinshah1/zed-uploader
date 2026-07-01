"""Async engine + session factory.

``expire_on_commit=False`` is required so that eager-loaded relationships such
as ``media.files`` remain accessible after ``session.commit()`` (the /start
handler reads them right after claiming a download).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a session and always close it."""
    async with async_session_maker() as session:
        yield session
