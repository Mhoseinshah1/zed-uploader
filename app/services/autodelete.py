"""Auto-delete queue backed by a Redis sorted set (Section 6.2).

Scheduled deletions are persisted in a Redis ZSET (score = due timestamp) so
they survive process restarts. A dedicated worker (app.workers.main) pops due
members and deletes the messages. Never use in-memory ``asyncio.create_task``.
"""
from __future__ import annotations

import json
import time

from redis.asyncio import Redis

QUEUE_KEY = "autodelete:queue"


class AutoDeleteQueue:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def schedule(
        self, chat_id: int, message_ids: list[int], seconds: int
    ) -> None:
        if seconds <= 0 or not message_ids:
            return
        due = time.time() + seconds
        await self.redis.zadd(
            QUEUE_KEY,
            {json.dumps({"c": chat_id, "m": m}): due for m in message_ids},
        )

    async def pop_due(self, limit: int = 100) -> list[str]:
        return await self.redis.zrangebyscore(
            QUEUE_KEY, min="-inf", max=time.time(), start=0, num=limit
        )

    async def ack(self, members: list[str]) -> None:
        if members:
            await self.redis.zrem(QUEUE_KEY, *members)
