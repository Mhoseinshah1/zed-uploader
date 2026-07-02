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
    """FastAPI dependency: yield a session and always close it.

    F1: the panel/API serve the single platform tenant, so each request runs
    under the platform tenant context (the DB guard filters/stamps by it, and
    fails closed otherwise). Later phases bind the customer's tenant here.
    """
    from app.core.tenant_context import PLATFORM_TENANT_ID, reset_tenant, set_tenant

    token = set_tenant(PLATFORM_TENANT_ID)
    try:
        async with async_session_maker() as session:
            yield session
    finally:
        reset_tenant(token)


# Register the tenant-isolation guard (events on the shared Session class) as a
# side effect of importing the session module — which is imported everywhere.
from app.db import tenant_scope  # noqa: E402,F401
