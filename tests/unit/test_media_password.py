"""A2 unit tests — media password hashing, verify, and Redis lockout.

No DB/network: bcrypt is pure-Python-callable and Redis is faked by the
suite-wide conftest fixture.
"""
from __future__ import annotations

import asyncio

from app.core.redis_client import get_redis
from app.core.security import (
    MEDIA_PW_MAX_FAILURES,
    clear_media_password_failures,
    hash_media_password,
    media_password_locked,
    record_media_password_failure,
    verify_media_password,
)
from app.models.media import Media
from app.services.media_service import MediaService


# --- hashing ---------------------------------------------------------------
def test_hash_is_bcrypt_and_not_plaintext():
    h = hash_media_password("s3cret")
    assert h != "s3cret"
    assert h.startswith("$2")  # bcrypt marker
    # two hashes of the same password differ (random salt)
    assert h != hash_media_password("s3cret")


def test_verify_roundtrip():
    h = hash_media_password("correct horse")
    assert verify_media_password("correct horse", h) is True
    assert verify_media_password("wrong", h) is False


def test_verify_handles_garbage_hash():
    assert verify_media_password("x", "not-a-hash") is False


# --- MediaService.verify_password -----------------------------------------
def test_service_verify_no_password_is_open():
    media = Media(code="abc", password_hash=None)
    assert MediaService.verify_password(media, "anything") is True


def test_service_verify_matches():
    media = Media(code="abc", password_hash=hash_media_password("pw"))
    assert MediaService.verify_password(media, "pw") is True
    assert MediaService.verify_password(media, "nope") is False


# --- Redis lockout ---------------------------------------------------------
def test_lockout_after_max_failures():
    async def run():
        redis = get_redis()
        assert await media_password_locked(redis, 42, "code1") is False
        remaining = None
        for _ in range(MEDIA_PW_MAX_FAILURES):
            remaining = await record_media_password_failure(redis, 42, "code1")
        assert remaining == 0
        assert await media_password_locked(redis, 42, "code1") is True
        # a different code for the same user is independent
        assert await media_password_locked(redis, 42, "code2") is False
        # clearing releases the lock
        await clear_media_password_failures(redis, 42, "code1")
        assert await media_password_locked(redis, 42, "code1") is False

    asyncio.run(run())


def test_failure_returns_decreasing_remaining():
    async def run():
        redis = get_redis()
        first = await record_media_password_failure(redis, 7, "c")
        second = await record_media_password_failure(redis, 7, "c")
        assert first == MEDIA_PW_MAX_FAILURES - 1
        assert second == MEDIA_PW_MAX_FAILURES - 2

    asyncio.run(run())
