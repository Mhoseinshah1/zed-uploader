"""Force-join membership checks.

Fail-open: if the bot can't query a channel (not admin there / channel gone),
that channel is skipped rather than blocking the user.
"""
from __future__ import annotations

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import RequiredChannel
from app.services.channel_service import ChannelService

_NON_MEMBER_STATUSES = {"left", "kicked"}


async def unjoined_channels(
    bot: Bot, session: AsyncSession, user_id: int
) -> list[RequiredChannel]:
    """Return the active required channels the user has NOT joined."""
    out: list[RequiredChannel] = []
    for channel in await ChannelService(session).list_active():
        try:
            member = await bot.get_chat_member(channel.chat_id, user_id)
            if member.status in _NON_MEMBER_STATUSES:
                out.append(channel)
        except Exception:
            # bot not admin / channel gone -> fail-open, skip this channel
            continue
    return out
