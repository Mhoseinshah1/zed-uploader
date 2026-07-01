"""FastAPI dependencies: rate limiting (fail-open), API-key auth, DB session."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import constant_time_compare
from app.db.session import get_session

RATE_LIMIT, RATE_WINDOW = 60, 60


async def rate_limit(request: Request) -> None:
    """60 requests / 60s per client IP. Fail-open if Redis is unavailable."""
    from app.core.redis_client import get_redis

    key = f"ratelimit:api:{request.client.host if request.client else 'unknown'}"
    try:
        count = await get_redis().incr(key)
        if count == 1:
            await get_redis().expire(key, RATE_WINDOW)
    except Exception:
        return  # fail-open
    if count > RATE_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests")


RateLimitDep = Depends(rate_limit)


async def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Validate the ``X-API-Key`` header against ``settings.api_key``."""
    if not x_api_key or not constant_time_compare(x_api_key, settings.api_key):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or missing API key"
        )


ApiKeyDep = Depends(require_api_key)

DbSession = Annotated[AsyncSession, Depends(get_session)]
