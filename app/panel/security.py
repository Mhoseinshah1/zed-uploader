"""Panel security primitives: bcrypt, CSRF, cookie signing, login rate limit."""
from __future__ import annotations

import hashlib
import hmac
import secrets

import bcrypt

from app.core.config import settings

_BCRYPT_MAX_BYTES = 72

# --- passwords -------------------------------------------------------------
def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(pw, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --- CSRF ------------------------------------------------------------------
def generate_csrf() -> str:
    return secrets.token_urlsafe(32)


def verify_csrf(token: str | None, session_token: str | None) -> bool:
    if not token or not session_token:
        return False
    return hmac.compare_digest(token, session_token)


# --- signed cookie value (holds only a random session id) ------------------
def _sig(value: str) -> str:
    return hmac.new(
        settings.session_secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def sign(value: str) -> str:
    return f"{value}.{_sig(value)}"


def unsign(signed: str | None) -> str | None:
    if not signed or "." not in signed:
        return None
    value, _, sig = signed.rpartition(".")
    if not value or not hmac.compare_digest(sig, _sig(value)):
        return None
    return value


# --- login rate limit (Redis fixed window, per IP AND per username) --------
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = 15 * 60  # 15 minutes


def _fail_keys(ip: str, username: str) -> tuple[str, str]:
    return (f"panel:loginfail:ip:{ip}", f"panel:loginfail:user:{username.lower()}")


async def login_locked(redis, ip: str, username: str) -> bool:
    for key in _fail_keys(ip, username):
        value = await redis.get(key)
        if value is not None and int(value) >= LOGIN_MAX_FAILURES:
            return True
    return False


async def record_login_failure(redis, ip: str, username: str) -> None:
    for key in _fail_keys(ip, username):
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, LOGIN_WINDOW)


async def clear_login_failures(redis, ip: str, username: str) -> None:
    await redis.delete(*_fail_keys(ip, username))
