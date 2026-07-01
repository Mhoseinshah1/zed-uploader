"""Security helpers: password hashing, timing-safe compare, media-password
brute-force lockout.

Media passwords are hashed with **bcrypt** (the same primitive the web panel
uses for admin logins) — never stored in plaintext, never SHA-256. The pbkdf2
scheme used before Phase A2 is gone; there are no persisted media passwords yet
so no migration of existing hashes is required.
"""
from __future__ import annotations

import hmac

import bcrypt

_BCRYPT_MAX_BYTES = 72  # bcrypt silently truncates beyond this; cap explicitly


# --- media password hashing (bcrypt) ---------------------------------------
def hash_media_password(password: str) -> str:
    """Hash a plaintext media password with bcrypt."""
    pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_media_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext media password against a stored bcrypt hash."""
    try:
        pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(pw, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def constant_time_compare(left: str, right: str) -> bool:
    """Timing-safe string comparison."""
    return hmac.compare_digest(left, right)


# --- per-(user, code) media-password lockout (Redis fixed window) ----------
MEDIA_PW_MAX_FAILURES = 3
MEDIA_PW_LOCK_TTL = 5 * 60  # seconds a user stays locked out of one code


def _media_pw_fail_key(user_id: int, code: str) -> str:
    return f"mediapw:fail:{user_id}:{code}"


async def media_password_locked(redis, user_id: int, code: str) -> bool:
    """True once the user has burned all attempts for this code."""
    value = await redis.get(_media_pw_fail_key(user_id, code))
    return value is not None and int(value) >= MEDIA_PW_MAX_FAILURES


async def record_media_password_failure(redis, user_id: int, code: str) -> int:
    """Count one wrong attempt; return remaining attempts (0 = now locked).

    The counter carries a fixed TTL, so the lockout releases itself.
    """
    key = _media_pw_fail_key(user_id, code)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, MEDIA_PW_LOCK_TTL)
    return max(0, MEDIA_PW_MAX_FAILURES - int(count))


async def clear_media_password_failures(redis, user_id: int, code: str) -> None:
    await redis.delete(_media_pw_fail_key(user_id, code))
