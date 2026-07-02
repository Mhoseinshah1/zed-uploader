"""BotPlanService — master-bot pricing CRUD (Phase F3, platform-global)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bot_plan import BotPlan


class BotPlanService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str) -> BotPlan | None:
        return await self.session.scalar(select(BotPlan).where(BotPlan.key == key))

    async def list_all(self) -> list[BotPlan]:
        rows = await self.session.scalars(select(BotPlan).order_by(BotPlan.id))
        return list(rows.all())

    async def list_active(self) -> list[BotPlan]:
        rows = await self.session.scalars(
            select(BotPlan).where(BotPlan.is_active.is_(True)).order_by(BotPlan.price)
        )
        return list(rows.all())

    async def upsert(
        self, key: str, title: str, price: int, duration_days: int,
        is_active: bool = True,
    ) -> BotPlan:
        plan = await self.get(key)
        if plan is None:
            plan = BotPlan(
                key=key, title=title, price=max(0, price),
                duration_days=max(0, duration_days), is_active=is_active,
            )
            self.session.add(plan)
        else:
            plan.title = title
            plan.price = max(0, price)
            plan.duration_days = max(0, duration_days)
            plan.is_active = is_active
        await self.session.commit()
        return plan

    async def set_active(self, key: str, is_active: bool) -> bool:
        plan = await self.get(key)
        if plan is None:
            return False
        plan.is_active = is_active
        await self.session.commit()
        return True

    async def delete(self, key: str) -> bool:
        plan = await self.get(key)
        if plan is None:
            return False
        await self.session.delete(plan)
        await self.session.commit()
        return True
