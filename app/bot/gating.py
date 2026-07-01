"""Plan/feature gating helpers for handlers.

Env owners bypass every gate (treated as unlimited / max plan).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.admin_service import AdminService
from app.services.feature_service import FeatureService
from app.services.media_service import MediaService
from app.services.plan_service import PlanService


async def feature_allowed(
    session: AsyncSession, feature_key: str, user: User | None, telegram_id: int
) -> bool:
    if AdminService.is_env_owner(telegram_id):
        return True
    if user is None:
        return False
    return await FeatureService.is_enabled(session, feature_key, user)


async def within_file_limit(
    session: AsyncSession, user: User | None, telegram_id: int
) -> bool:
    """True if the owner may create another media item under their plan."""
    if AdminService.is_env_owner(telegram_id):
        return True
    if user is None:
        return False
    limit = await PlanService(session).max_files(user.effective_plan)
    if limit is None:
        return True
    count = await MediaService(session).count_by_owner(user.id)
    return count < limit
