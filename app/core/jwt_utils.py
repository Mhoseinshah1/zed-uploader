"""Minimal HS256 JWT (dependency-free) for the v1 admin API.

Signed with ``settings.jwt_secret``. Only what we need: exp validation and a
constant-time signature check. Tokens carry the panel user id as ``sub``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from app.core.config import settings

DEFAULT_TTL = 12 * 3600  # seconds


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(msg: bytes) -> str:
    return _b64(
        hmac.new(settings.jwt_secret.encode(), msg, hashlib.sha256).digest()
    )


def encode(sub: int, ttl: int = DEFAULT_TTL) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(
        json.dumps({"sub": sub, "exp": int(time.time()) + ttl}).encode()
    )
    msg = f"{header}.{payload}".encode()
    return f"{header}.{payload}.{_sign(msg)}"


def decode(token: str) -> dict | None:
    """Return the payload for a valid unexpired token, else None."""
    try:
        header, payload, signature = token.split(".")
        expected = _sign(f"{header}.{payload}".encode())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        if int(data.get("exp", 0)) < time.time():
            return None
        return data
    except Exception:
        return None
