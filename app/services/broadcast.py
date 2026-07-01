"""Broadcast queue helpers shared by the enqueuing handler and the worker.

The heavy lifting (actual sending) lives in the worker loop; this module only
holds the Redis keys, the enqueue call, and an audience count.
"""
from __future__ import annotations

import json

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

QUEUE_KEY = "broadcast:queue"
ACTIVE_KEY = "broadcast:active"
PAGE_SIZE = 100
SEND_DELAY = 0.05  # ~20 messages/sec, safely under Telegram limits


async def enqueue(
    redis: Redis, *, from_chat_id: int, message_id: int, requested_by: int
) -> None:
    job = {
        "from_chat_id": from_chat_id,
        "message_id": message_id,
        "cursor_id": 0,
        "requested_by": requested_by,
        "sent": 0,
        "failed": 0,
    }
    await redis.rpush(QUEUE_KEY, json.dumps(job))


async def audience_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count(User.id))) or 0)
