"""AlbumBuffer — Redis buffer for Telegram albums (media_group_id).

Telegram delivers an album as several separate messages that share a
``media_group_id``, arriving within ~1s. We buffer each part in Redis and let the
worker finalize the group after a short debounce (reset on every new part).

Why worker-driven (not an in-process asyncio timer): the buffer lives in Redis,
so it survives a bot/api restart mid-album — the worker just finalizes whatever
is due on its next tick. It also does not depend on all parts hitting the same
in-memory process (robust even if the webhook process is replaced). The cost is
a small latency (debounce + one worker poll), which is fine for uploads.
"""
from __future__ import annotations

import json

from redis.asyncio import Redis

DUE_KEY = "album:due"          # ZSET gk -> finalize-at (epoch seconds)
_DATA = "album:data"           # LIST gk -> json parts in arrival order
_META = "album:meta"           # HASH gk -> {chat_id, telegram_id}
DEBOUNCE = 2.0                  # seconds after the last part before finalizing
TTL = 300                      # safety expiry on buffered parts


class AlbumBuffer:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    @staticmethod
    def group_key(tenant_id: int, chat_id: int, media_group_id: str) -> str:
        # key by tenant too, so two tenants' albums never merge.
        return f"{tenant_id}:{chat_id}:{media_group_id}"

    async def add(
        self,
        group_key: str,
        *,
        tenant_id: int,
        chat_id: int,
        telegram_id: int,
        part: dict,
        now: float,
        debounce: float = DEBOUNCE,
        ttl: int = TTL,
    ) -> None:
        data_key = f"{_DATA}:{group_key}"
        meta_key = f"{_META}:{group_key}"
        await self.redis.rpush(data_key, json.dumps(part))
        await self.redis.expire(data_key, ttl)
        await self.redis.hset(
            meta_key,
            mapping={
                "tenant_id": str(tenant_id),
                "chat_id": str(chat_id),
                "telegram_id": str(telegram_id),
            },
        )
        await self.redis.expire(meta_key, ttl)
        # ZADD overwrites the score -> every new part pushes the finalize time out
        await self.redis.zadd(DUE_KEY, {group_key: now + debounce})

    async def pop_due(self, now: float) -> list[dict]:
        """Claim and return the groups whose debounce has elapsed.

        Each returned dict is {group_key, chat_id, telegram_id, parts:[...]}.
        Claiming via ZREM makes a group finalize at most once.
        """
        group_keys = await self.redis.zrangebyscore(DUE_KEY, min="-inf", max=now)
        out: list[dict] = []
        for gk in group_keys:
            if not await self.redis.zrem(DUE_KEY, gk):
                continue  # someone else claimed it
            data_key = f"{_DATA}:{gk}"
            meta_key = f"{_META}:{gk}"
            raw_parts = await self.redis.lrange(data_key, 0, -1)
            meta = await self.redis.hgetall(meta_key)
            await self.redis.delete(data_key, meta_key)
            if not raw_parts:
                continue
            out.append(
                {
                    "group_key": gk,
                    "tenant_id": int(meta.get("tenant_id", 0)),
                    "chat_id": int(meta.get("chat_id", 0)),
                    "telegram_id": int(meta.get("telegram_id", 0)),
                    "parts": [json.loads(p) for p in raw_parts],
                }
            )
        return out
