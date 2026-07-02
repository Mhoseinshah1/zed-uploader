"""Short-lived, single-use, tenant-scoped panel auth links (G3).

An in-bot admin taps a button that deep-links to a panel page without ever
typing or seeing a secret. The link carries a random token that:
  - EXPIRES quickly  — a Redis TTL (``LINK_TTL`` seconds);
  - is SINGLE-USE    — consumed with an atomic GETDEL, so a replay finds nothing;
  - is TENANT-SCOPED — it binds a specific tenant's panel_user, and the created
                        session is that user's (F4 confines it to that tenant).
A token minted for tenant A can therefore only ever open tenant A's panel.
"""
from __future__ import annotations

import json
import secrets

LINK_PREFIX = "panellink:"
LINK_TTL = 300  # seconds (5 minutes)


class PanelLinkService:
    def __init__(self, redis) -> None:
        self.redis = redis

    async def mint(self, *, tenant_id: int, panel_user_id: int, target: str) -> str:
        token = secrets.token_urlsafe(32)
        payload = json.dumps({"t": tenant_id, "u": panel_user_id, "p": target})
        await self.redis.set(LINK_PREFIX + token, payload, ex=LINK_TTL)
        return token

    async def consume(self, token: str) -> dict | None:
        """Atomically fetch+delete the token (single use). None if used/expired."""
        if not token:
            return None
        key = LINK_PREFIX + token
        try:
            raw = await self.redis.getdel(key)
        except Exception:
            # fallback for backends without GETDEL: get then delete
            raw = await self.redis.get(key)
            if raw is not None:
                await self.redis.delete(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None
