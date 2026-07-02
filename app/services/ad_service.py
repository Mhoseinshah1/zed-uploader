"""AdService — pick ads for a placement + best-effort counters + owner CRUD.

Counter increments are single atomic UPDATEs so concurrent deliveries never
lose counts; callers treat every ad operation as best-effort (an ad failure
must never break file delivery).
"""
from __future__ import annotations

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad import Ad

MAX_ADS_PER_PLACEMENT = 3  # bounded: never spam more than this per event


class AdService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # delivery-side
    # ------------------------------------------------------------------
    async def pick_for_placement(self, placement: str, plan: str) -> list[Ad]:
        """Active ads for a placement, honoring target_plan + impression_limit."""
        result = await self.session.scalars(
            select(Ad)
            .where(
                Ad.placement == placement,
                Ad.is_active.is_(True),
                or_(Ad.target_plan.is_(None), Ad.target_plan == plan),
                or_(
                    Ad.impression_limit.is_(None),
                    Ad.impression_count < Ad.impression_limit,
                ),
            )
            .order_by(Ad.id)
            .limit(MAX_ADS_PER_PLACEMENT)
        )
        return list(result.all())

    async def record_impression(self, ad_id: int) -> None:
        await self.session.execute(
            update(Ad).where(Ad.id == ad_id).values(
                impression_count=Ad.impression_count + 1
            )
        )
        await self.session.commit()

    async def record_click(self, ad_id: int) -> str | None:
        """Count a click and return the target URL (None when unavailable)."""
        ad = await self.session.get(Ad, ad_id)
        if ad is None or not ad.button_url:
            return None
        await self.session.execute(
            update(Ad).where(Ad.id == ad_id).values(click_count=Ad.click_count + 1)
        )
        await self.session.commit()
        return ad.button_url

    # ------------------------------------------------------------------
    # owner CRUD
    # ------------------------------------------------------------------
    async def get(self, ad_id: int) -> Ad | None:
        return await self.session.get(Ad, ad_id)

    async def list_all(self, *, limit: int = 50) -> list[Ad]:
        result = await self.session.scalars(
            select(Ad).order_by(Ad.id.desc()).limit(limit)
        )
        return list(result.all())

    async def count_all(self) -> int:
        return int(await self.session.scalar(select(func.count(Ad.id))) or 0)

    async def create(
        self,
        *,
        title: str,
        text: str,
        placement: str,
        button_text: str | None = None,
        button_url: str | None = None,
        target_plan: str | None = None,
        impression_limit: int | None = None,
    ) -> Ad:
        ad = Ad(
            title=title.strip(),
            text=text,
            placement=placement,
            button_text=(button_text or "").strip() or None,
            button_url=(button_url or "").strip() or None,
            target_plan=(target_plan or "").strip() or None,
            impression_limit=impression_limit,
        )
        self.session.add(ad)
        await self.session.commit()
        return ad

    async def update_fields(self, ad_id: int, **values) -> bool:
        ad = await self.get(ad_id)
        if ad is None:
            return False
        for field, value in values.items():
            setattr(ad, field, value)
        await self.session.commit()
        return True

    async def toggle(self, ad_id: int) -> bool:
        ad = await self.get(ad_id)
        if ad is None:
            return False
        ad.is_active = not ad.is_active
        await self.session.commit()
        return True

    async def delete(self, ad_id: int) -> bool:
        ad = await self.get(ad_id)
        if ad is None:
            return False
        await self.session.delete(ad)
        await self.session.commit()
        return True
