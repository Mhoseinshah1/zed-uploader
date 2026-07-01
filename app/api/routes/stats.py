"""Stats endpoint — protected by rate limit + API key."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import ApiKeyDep, DbSession, RateLimitDep
from app.schemas.stats import StatsOut
from app.services.media_service import MediaService
from app.services.user_service import UserService

router = APIRouter(
    prefix="/api/stats",
    tags=["stats"],
    dependencies=[RateLimitDep, ApiKeyDep],
)


@router.get("", response_model=StatsOut)
async def get_stats(session: DbSession) -> StatsOut:
    media_service = MediaService(session)
    user_service = UserService(session)
    return StatsOut(
        total_users=await user_service.count_users(),
        total_media=await media_service.count_media(),
        total_downloads=await media_service.total_downloads(),
    )
