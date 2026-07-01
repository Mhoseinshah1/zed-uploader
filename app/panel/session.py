"""Redis-backed server-side sessions for the panel.

The cookie carries only a signed random session id; all session state lives in
Redis with a sliding TTL.
"""
from __future__ import annotations

import json
import secrets

COOKIE_NAME = "zpsid"
SESSION_PREFIX = "panel:session:"
SESSION_TTL = 12 * 60 * 60  # 12h, sliding


class SessionStore:
    def __init__(self, redis) -> None:
        self.redis = redis

    async def create(self, data: dict) -> str:
        sid = secrets.token_urlsafe(32)
        await self.redis.set(
            SESSION_PREFIX + sid, json.dumps(data), ex=SESSION_TTL
        )
        return sid

    async def get(self, sid: str) -> dict | None:
        raw = await self.redis.get(SESSION_PREFIX + sid)
        if raw is None:
            return None
        # sliding expiry
        await self.redis.expire(SESSION_PREFIX + sid, SESSION_TTL)
        return json.loads(raw)

    async def delete(self, sid: str) -> None:
        await self.redis.delete(SESSION_PREFIX + sid)
