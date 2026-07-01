"""A2 integration — password gate in the delivery flow (REAL Postgres).

Uses a mock Bot: with no required channels the force-join check never touches
it, and ``send_media_file`` is monkeypatched so we assert on delivery status +
the download claim without hitting Telegram.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import app.bot.delivery as delivery
from app.bot.delivery import DeliveryStatus, deliver_by_code
from app.models import Media, MediaFile, User
from app.services.media_service import MediaService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


async def _seed_media(maker, *, code: str, password: str | None) -> int:
    async with maker() as s:
        media = Media(code=code)
        media.files.append(MediaFile(sort_order=0, telegram_file_id="fid", file_type="document"))
        s.add(media)
        await s.commit()
        mid = media.id
        if password is not None:
            # set_password is owner-scoped; seed with a null owner match by
            # writing the hash directly through the service helper.
            from app.core.security import hash_media_password

            media.password_hash = hash_media_password(password)
            await s.commit()
    return mid


async def _download_count(maker, mid: int) -> int:
    async with maker() as s:
        media = await s.get(Media, mid)
        return media.download_count


def _mock_bot() -> AsyncMock:
    return AsyncMock()


# password-protected file is gated before any download claim ------------------
async def test_password_required_blocks_and_does_not_claim(pg_sessionmaker, monkeypatch):
    sent = AsyncMock(return_value=123)
    monkeypatch.setattr(delivery, "send_media_file", sent)
    mid = await _seed_media(pg_sessionmaker, code="secret1", password="letmein")

    async with pg_sessionmaker() as s:
        result = await deliver_by_code(_mock_bot(), s, 555, None, "secret1")

    assert result.status is DeliveryStatus.PASSWORD_REQUIRED
    sent.assert_not_called()  # nothing sent
    assert await _download_count(pg_sessionmaker, mid) == 0  # no claim


# with the password verified, delivery proceeds and claims exactly one slot ---
async def test_password_verified_delivers_and_claims(pg_sessionmaker, monkeypatch):
    sent = AsyncMock(return_value=123)
    monkeypatch.setattr(delivery, "send_media_file", sent)
    mid = await _seed_media(pg_sessionmaker, code="secret2", password="letmein")

    async with pg_sessionmaker() as s:
        result = await deliver_by_code(
            _mock_bot(), s, 555, None, "secret2", password_verified=True
        )

    assert result.status is DeliveryStatus.DELIVERED
    sent.assert_awaited_once()
    assert await _download_count(pg_sessionmaker, mid) == 1


# a file with no password delivers openly (no gate) --------------------------
async def test_no_password_delivers_openly(pg_sessionmaker, monkeypatch):
    monkeypatch.setattr(delivery, "send_media_file", AsyncMock(return_value=1))
    mid = await _seed_media(pg_sessionmaker, code="open1", password=None)

    async with pg_sessionmaker() as s:
        result = await deliver_by_code(_mock_bot(), s, 555, None, "open1")

    assert result.status is DeliveryStatus.DELIVERED
    assert await _download_count(pg_sessionmaker, mid) == 1


# removing a password restores open access -----------------------------------
async def test_clear_password_restores_open_access(pg_sessionmaker, monkeypatch):
    monkeypatch.setattr(delivery, "send_media_file", AsyncMock(return_value=1))
    mid = await _seed_media(pg_sessionmaker, code="secret3", password="letmein")

    # owner-scoped clear: owner is NULL here, so clear directly like the panel does
    async with pg_sessionmaker() as s:
        media = await s.get(Media, mid)
        media.password_hash = None
        await s.commit()

    async with pg_sessionmaker() as s:
        result = await deliver_by_code(_mock_bot(), s, 555, None, "secret3")

    assert result.status is DeliveryStatus.DELIVERED
    assert await _download_count(pg_sessionmaker, mid) == 1


# service-level set/verify with a real owner ---------------------------------
async def test_service_set_and_verify_with_owner(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        user = User(telegram_id=901)
        s.add(user)
        media = Media(code="owned1", owner_user_id=None)
        media.files.append(MediaFile(sort_order=0, telegram_file_id="f", file_type="document"))
        s.add(media)
        await s.commit()
        # attach ownership so the owner-scoped mutator matches
        media.owner_user_id = user.id
        await s.commit()

        svc = MediaService(s)
        assert await svc.set_password(media.id, user.id, "pw") is True
        refreshed = await s.get(Media, media.id)
        await s.refresh(refreshed)
        assert MediaService.verify_password(refreshed, "pw") is True
        assert MediaService.verify_password(refreshed, "bad") is False

        assert await svc.clear_password(media.id, user.id) is True
        refreshed = await s.get(Media, media.id)
        await s.refresh(refreshed)
        assert refreshed.password_hash is None
        # wrong owner is a no-op
        assert await svc.set_password(media.id, user.id + 999, "x") is False
