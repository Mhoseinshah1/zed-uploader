"""Service for the ``required_channels`` (force-join) table."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import RequiredChannel


class ChannelService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self) -> list[RequiredChannel]:
        result = await self.session.scalars(
            select(RequiredChannel)
            .where(RequiredChannel.is_active.is_(True))
            .order_by(RequiredChannel.id)
        )
        return list(result.all())

    async def list_all(self) -> list[RequiredChannel]:
        result = await self.session.scalars(
            select(RequiredChannel).order_by(RequiredChannel.id)
        )
        return list(result.all())

    async def get(self, channel_id: int) -> RequiredChannel | None:
        return await self.session.scalar(
            select(RequiredChannel).where(RequiredChannel.id == channel_id)
        )

    async def add(
        self,
        chat_id: str,
        title: str | None = None,
        invite_link: str | None = None,
    ) -> RequiredChannel:
        channel = RequiredChannel(
            chat_id=chat_id, title=title, invite_link=invite_link, is_active=True
        )
        self.session.add(channel)
        await self.session.commit()
        return channel

    async def remove(self, channel_id: int) -> bool:
        channel = await self.get(channel_id)
        if channel is None:
            return False
        await self.session.delete(channel)
        await self.session.commit()
        return True

    async def toggle(self, channel_id: int) -> bool:
        channel = await self.get(channel_id)
        if channel is None:
            return False
        channel.is_active = not channel.is_active
        await self.session.commit()
        return True
