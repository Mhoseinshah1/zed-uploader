"""PlanService — read/update purchasable plans."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan import Plan


class PlanService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self) -> list[Plan]:
        result = await self.session.scalars(
            select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.id)
        )
        return list(result.all())

    async def get(self, key: str) -> Plan | None:
        return await self.session.scalar(select(Plan).where(Plan.key == key))

    async def set_price(self, key: str, price: int) -> bool:
        plan = await self.get(key)
        if plan is None:
            return False
        plan.price = price
        await self.session.commit()
        return True

    async def set_duration(self, key: str, days: int) -> bool:
        plan = await self.get(key)
        if plan is None:
            return False
        plan.duration_days = days
        await self.session.commit()
        return True

    async def max_files(self, plan_key: str) -> int | None:
        """Return the plan's max_files (None = unlimited)."""
        plan = await self.get(plan_key)
        return plan.max_files if plan is not None else None
