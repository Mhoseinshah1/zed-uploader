"""FeatureService — plan-gated feature flags."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plans import plan_rank
from app.models.settings import FeatureFlag
from app.models.user import User

# The panel-editable feature flags (I5). A flag's absence means "off".
FEATURE_KEYS = ("protect_content", "auto_delete", "batch_upload")


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

    @staticmethod
    async def list_flags(
        session: AsyncSession, keys: Iterable[str]
    ) -> dict[str, FeatureFlag]:
        rows = await session.scalars(
            select(FeatureFlag).where(FeatureFlag.key.in_(tuple(keys)))
        )
        return {f.key: f for f in rows}

    @staticmethod
    async def set_flag(
        session: AsyncSession, key: str, is_enabled: bool, plan: str | None
    ) -> FeatureFlag:
        """Upsert a feature flag (I5 panel). Tenant stamped by the guard."""
        flag = await FeatureService._flag(session, key)
        if flag is None:
            flag = FeatureFlag(key=key, is_enabled=is_enabled, plan=plan or None)
            session.add(flag)
        else:
            flag.is_enabled = is_enabled
            flag.plan = plan or None
        await session.commit()
        return flag
