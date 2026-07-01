"""Admin service — seeding, DB-aware authz, and admin CRUD.

Roles:
  owner = telegram id in env ADMIN_IDS (seeded role "owner") OR active Admin
          row with role "owner".
  admin = owner OR active Admin row with any role.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.admin import Admin


class AdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # seeding
    # ------------------------------------------------------------------
    async def ensure_seed_admins(self, admin_ids: list[int]) -> None:
        """Insert an owner Admin row for every configured env id that is missing."""
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

    # ------------------------------------------------------------------
    # DB-aware authz (static: shared by filters that receive the session)
    # ------------------------------------------------------------------
    @staticmethod
    def is_env_owner(telegram_id: int) -> bool:
        return telegram_id in settings.admin_id_list

    @staticmethod
    async def is_admin(session: AsyncSession, telegram_id: int) -> bool:
        """True for env ids OR any active Admin row."""
        if AdminService.is_env_owner(telegram_id):
            return True
        found = await session.scalar(
            select(Admin.id).where(
                Admin.telegram_id == telegram_id, Admin.is_active.is_(True)
            )
        )
        return found is not None

    @staticmethod
    async def is_owner(session: AsyncSession, telegram_id: int) -> bool:
        """True for env ids OR an active Admin row with role 'owner'."""
        if AdminService.is_env_owner(telegram_id):
            return True
        found = await session.scalar(
            select(Admin.id).where(
                Admin.telegram_id == telegram_id,
                Admin.is_active.is_(True),
                Admin.role == "owner",
            )
        )
        return found is not None

    # ------------------------------------------------------------------
    # CRUD (owners-only surface, guarded at handler level)
    # ------------------------------------------------------------------
    async def list_all(self) -> list[Admin]:
        result = await self.session.scalars(select(Admin).order_by(Admin.id))
        return list(result.all())

    async def get(self, admin_id: int) -> Admin | None:
        return await self.session.scalar(select(Admin).where(Admin.id == admin_id))

    async def get_by_telegram_id(self, telegram_id: int) -> Admin | None:
        return await self.session.scalar(
            select(Admin).where(Admin.telegram_id == telegram_id)
        )

    async def add_admin(self, telegram_id: int, role: str = "admin") -> Admin:
        """Create or reactivate an Admin row for a telegram id."""
        admin = await self.get_by_telegram_id(telegram_id)
        if admin is None:
            admin = Admin(telegram_id=telegram_id, role=role, is_active=True)
            self.session.add(admin)
        else:
            admin.is_active = True
            admin.role = role
        await self.session.commit()
        return admin

    async def set_active(self, admin_id: int, is_active: bool) -> bool:
        admin = await self.get(admin_id)
        if admin is None:
            return False
        admin.is_active = is_active
        await self.session.commit()
        return True

    async def remove(self, admin_id: int) -> bool:
        admin = await self.get(admin_id)
        if admin is None:
            return False
        await self.session.delete(admin)
        await self.session.commit()
        return True
