"""Admin service — seed admins from ADMIN_IDS and check admin status."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import Admin


class AdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ensure_seed_admins(self, admin_ids: list[int]) -> None:
        """Insert an Admin row for every configured id that is missing."""
        if not admin_ids:
            return
        existing = await self.session.scalars(
            select(Admin.telegram_id).where(Admin.telegram_id.in_(admin_ids))
        )
        known = set(existing.all())
        created = False
        for telegram_id in admin_ids:
            if telegram_id not in known:
                self.session.add(
                    Admin(telegram_id=telegram_id, role="owner", is_active=True)
                )
                created = True
        if created:
            await self.session.commit()

    async def is_admin(self, telegram_id: int) -> bool:
        admin_id = await self.session.scalar(
            select(Admin.id).where(
                Admin.telegram_id == telegram_id, Admin.is_active.is_(True)
            )
        )
        return admin_id is not None
