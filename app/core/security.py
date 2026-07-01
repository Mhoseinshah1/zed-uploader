"""Security helpers: password hashing and constant-time comparisons.

Uses only the standard library (PBKDF2-HMAC-SHA256) so no extra native
dependency is required. Media password protection is stored here but is not
enforced in the Phase 1 delivery flow (the field/schema keeps Phase 2 open).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 100_000


def hash_password(password: str) -> str:
    """Hash a plaintext password into a self-describing string."""
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS
    )
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a plaintext password against a previously hashed value."""
    try:
        algorithm, iterations, salt, hex_hash = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)
        )
        return hmac.compare_digest(derived.hex(), hex_hash)
    except (ValueError, AttributeError):
        return False


def constant_time_compare(left: str, right: str) -> bool:
    """Timing-safe string comparison."""
    return hmac.compare_digest(left, right)
