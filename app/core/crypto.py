"""Symmetric encryption for secrets at rest — bot tokens (Phase F1).

Fernet (AES-128-CBC + HMAC) with the key from ``TENANT_TOKEN_KEY``. Generate a
key once with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

The key is a config value (env), never committed. Ciphertext (not plaintext) is
what lands in ``tenants.bot_token``.
"""
from __future__ import annotations

from cryptography.fernet import Fernet

from app.core.config import settings


def _fernet() -> Fernet:
    key = settings.tenant_token_key
    if not key:
        raise RuntimeError("TENANT_TOKEN_KEY is not configured")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
