"""Media list endpoint — protected by rate limit + API key."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import ApiKeyDep, DbSession, RateLimitDep
from app.schemas.media import MediaOut
from app.services.media_service import MediaService

router = APIRouter(
    prefix="/api/media",
    tags=["media"],
    dependencies=[RateLimitDep, ApiKeyDep],
)


@router.get("", response_model=list[MediaOut])
async def list_media(
    session: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[MediaOut]:
    items = await MediaService(session).list_media(limit=limit, offset=offset)
    return [MediaOut.model_validate(item) for item in items]
