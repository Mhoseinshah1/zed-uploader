"""FeatureService — plan-gated feature flags."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plans import plan_rank
from app.models.settings import FeatureFlag
from app.models.user import User


class FeatureService:
    @staticmethod
    async def _flag(session: AsyncSession, feature_key: str) -> FeatureFlag | None:
        return await session.scalar(
            select(FeatureFlag).where(FeatureFlag.key == feature_key)
        )

    @staticmethod
    async def is_enabled(
        session: AsyncSession, feature_key: str, user: User
    ) -> bool:
        flag = await FeatureService._flag(session, feature_key)
        if flag is None or not flag.is_enabled:
            return False
        return plan_rank(user.effective_plan) >= plan_rank(flag.plan)

    @staticmethod
    async def required_plan(session: AsyncSession, feature_key: str) -> str:
        flag = await FeatureService._flag(session, feature_key)
        return (flag.plan if flag and flag.plan else "free")
