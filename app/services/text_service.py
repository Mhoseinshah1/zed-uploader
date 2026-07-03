"""Editable bot texts (D3): DB override -> built-in Persian default.

Overrides live in ``bot_settings`` under ``text:<key>`` (no new table). Each
resolved value is cached in Redis with a short TTL and cache-busted on save; a
missing/broken Redis degrades gracefully to the DB/default (never crashes a
handler).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.core.redis_client import get_redis

CACHE_TTL = 60  # seconds — panel edits go live within a minute at worst

# key -> built-in default (the panel shows these as the preview/placeholder)
OVERRIDABLE_TEXTS: dict[str, str] = {
    "welcome": messages.WELCOME,
    "help": messages.HELP,
    "not_found": messages.NOT_FOUND,
    "inactive": messages.INACTIVE,
    "limit_reached": messages.LIMIT_REACHED,
    "force_join": messages.GATE_PROMPT,
    "upload_disabled": messages.NOT_ADMIN_UPLOAD,
    "generic_error": messages.GENERIC_ERROR,
    "upload_pending_review": messages.UPLOAD_PENDING_REVIEW,
    "password_prompt": messages.PASSWORD_PROMPT,
    "account_blocked": messages.ACCOUNT_BLOCKED,
}


def _cache_key(key: str) -> str:
    return f"bottext:{key}"


def _setting_key(key: str) -> str:
    return f"text:{key}"


async def get_text(session: AsyncSession, key: str) -> str:
    """Resolve a user-facing text: Redis cache -> DB override -> default."""
    default = OVERRIDABLE_TEXTS.get(key, "")
    try:
        cached = await get_redis().get(_cache_key(key))
        if cached:
            return cached
    except Exception:
        pass  # Redis down -> fall through to DB/default

    from app.services.bot_setting_service import BotSettingService

    try:
        override = await BotSettingService(session).get_raw(_setting_key(key))
    except Exception:
        override = None
    value = override.strip() if override and override.strip() else default
    try:
        await get_redis().set(_cache_key(key), value, ex=CACHE_TTL)
    except Exception:
        pass
    return value


async def set_text(session: AsyncSession, key: str, value: str) -> None:
    """Save an override ('' clears it -> default) and bust the cache."""
    if key not in OVERRIDABLE_TEXTS:
        return
    from app.services.bot_setting_service import BotSettingService

    await BotSettingService(session).set(_setting_key(key), value.strip())
    try:
        await get_redis().delete(_cache_key(key))
    except Exception:
        pass  # worst case: the old value lives for CACHE_TTL more seconds
