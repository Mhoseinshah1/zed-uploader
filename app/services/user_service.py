"""User service — upsert Telegram users and list/count them for the API."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.session.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )

    async def upsert_from_telegram(self, tg_user: Any) -> User:
        """Create or update a :class:`User` from an aiogram ``types.User``."""
        user = await self.get_by_telegram_id(tg_user.id)
        if user is None:
            user = User(
                telegram_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                language_code=tg_user.language_code,
            )
            self.session.add(user)
        else:
            user.username = tg_user.username
            user.first_name = tg_user.first_name
            user.last_name = tg_user.last_name
            user.language_code = tg_user.language_code
        await self.session.commit()
        return user

    async def list_users(self, *, limit: int = 50, offset: int = 0) -> list[User]:
        result = await self.session.scalars(
            select(User).order_by(User.id.desc()).limit(limit).offset(offset)
        )
        return list(result.all())

    async def count_users(self) -> int:
        return int(await self.session.scalar(select(func.count(User.id))) or 0)
