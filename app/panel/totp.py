"""RFC 6238 TOTP — stdlib only (hmac/sha1), no external dependency (J9).

Standard parameters (Google Authenticator compatible): 6 digits, 30s step,
SHA-1. Verification allows ±1 step of clock drift and compares digits with
``hmac.compare_digest``.
"""
from __future__ import annotations

import base64
import hmac
import secrets
import struct
import time
from hashlib import sha1
from urllib.parse import quote

DIGITS = 6
STEP = 30  # seconds
ISSUER = "ZedUploader"


def generate_secret() -> str:
    """A fresh 160-bit base32 secret (the RFC 4226 recommended size)."""
    return base64.b32encode(secrets.token_bytes(20)).decode()


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** DIGITS)).zfill(DIGITS)


def totp_at(secret_b32: str, timestamp: float) -> str:
    return _hotp(secret_b32, int(timestamp) // STEP)


def verify_totp(secret_b32: str, code: str, window: int = 1) -> bool:
    """Constant-time check of ``code`` against now ±``window`` steps."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return False
    counter = int(time.time()) // STEP
    ok = False
    for drift in range(-window, window + 1):
        expected = _hotp(secret_b32, counter + drift)
        # no early exit — keep the comparison count constant
        ok = hmac.compare_digest(expected, code) or ok
    return ok


def provisioning_uri(secret_b32: str, username: str) -> str:
    """otpauth:// URI the user can paste into any authenticator app."""
    label = quote(f"{ISSUER}:{username}")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}"
        f"&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP}"
    )
