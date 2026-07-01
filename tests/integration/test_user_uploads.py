"""B1 integration (REAL Postgres): review lifecycle + delivery gating.

The security-critical rule: a pending/rejected media must NEVER be retrievable
by code. Delivery is exercised with a mock bot (no required channels => the
force-join check never touches it; send_media_file is monkeypatched).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import app.bot.delivery as delivery
from app.bot.delivery import DeliveryStatus, deliver_by_code
from app.models import Media, User
from app.services.media_service import MediaService, MediaStatus
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


async def _user(maker, telegram_id: int) -> int:
    async with maker() as s:
        u = User(telegram_id=telegram_id)
        s.add(u)
        await s.commit()
        return u.id


async def _make(maker, *, code_owner: int, status: str) -> int:
    async with maker() as s:
        media = await MediaService(s).create_media(
            files=[{"telegram_file_id": "fid", "file_type": "document"}],
            owner_user_id=code_owner,
            status=status,
        )
        return media.id


async def _code(maker, media_id: int) -> str:
    async with maker() as s:
        m = await s.get(Media, media_id)
        return m.code


async def _count(maker, media_id: int) -> int:
    async with maker() as s:
        m = await s.get(Media, media_id)
        return m.download_count


def _bot() -> AsyncMock:
    return AsyncMock()


# --- delivery gating (the critical part) -----------------------------------
async def test_pending_not_deliverable(pg_sessionmaker, monkeypatch):
    sent = AsyncMock(return_value=1)
    monkeypatch.setattr(delivery, "send_media_file", sent)
    uid = await _user(pg_sessionmaker, 9001)
    mid = await _make(pg_sessionmaker, code_owner=uid, status="pending")
    code = await _code(pg_sessionmaker, mid)

    async with pg_sessionmaker() as s:
        result = await deliver_by_code(_bot(), s, 555, None, code)
    assert result.status is DeliveryStatus.NOT_FOUND
    sent.assert_not_called()
    assert await _count(pg_sessionmaker, mid) == 0


async def test_rejected_not_deliverable(pg_sessionmaker, monkeypatch):
    monkeypatch.setattr(delivery, "send_media_file", AsyncMock(return_value=1))
    uid = await _user(pg_sessionmaker, 9002)
    mid = await _make(pg_sessionmaker, code_owner=uid, status="rejected")
    code = await _code(pg_sessionmaker, mid)

    async with pg_sessionmaker() as s:
        result = await deliver_by_code(_bot(), s, 555, None, code)
    assert result.status is DeliveryStatus.NOT_FOUND
    assert await _count(pg_sessionmaker, mid) == 0


async def test_approved_deliverable(pg_sessionmaker, monkeypatch):
    monkeypatch.setattr(delivery, "send_media_file", AsyncMock(return_value=1))
    uid = await _user(pg_sessionmaker, 9003)
    mid = await _make(pg_sessionmaker, code_owner=uid, status="approved")
    code = await _code(pg_sessionmaker, mid)

    async with pg_sessionmaker() as s:
        result = await deliver_by_code(_bot(), s, 555, None, code)
    assert result.status is DeliveryStatus.DELIVERED
    assert await _count(pg_sessionmaker, mid) == 1


async def test_check_status_and_claim_gate(pg_sessionmaker):
    uid = await _user(pg_sessionmaker, 9004)
    pending = await _make(pg_sessionmaker, code_owner=uid, status="pending")
    approved = await _make(pg_sessionmaker, code_owner=uid, status="approved")

    async with pg_sessionmaker() as s:
        svc = MediaService(s)
        pcode = (await s.get(Media, pending)).code
        acode = (await s.get(Media, approved)).code
        assert await svc.check_status(pcode) is MediaStatus.NOT_FOUND
        assert await svc.check_status(acode) is MediaStatus.OK
        status, _ = await svc.try_claim_download(pcode)
        assert status is MediaStatus.NOT_FOUND


# --- review transitions -----------------------------------------------------
async def test_approve_makes_deliverable(pg_sessionmaker, monkeypatch):
    monkeypatch.setattr(delivery, "send_media_file", AsyncMock(return_value=1))
    uid = await _user(pg_sessionmaker, 9005)
    mid = await _make(pg_sessionmaker, code_owner=uid, status="pending")

    async with pg_sessionmaker() as s:
        media = await MediaService(s).approve(mid, admin_id=42)
        assert media is not None
    async with pg_sessionmaker() as s:
        m = await s.get(Media, mid)
        assert m.status == "approved" and m.approved_at is not None
        assert m.reviewed_by_admin_id == 42
        code = m.code

    async with pg_sessionmaker() as s:
        result = await deliver_by_code(_bot(), s, 555, None, code)
    assert result.status is DeliveryStatus.DELIVERED


async def test_reject_sets_note(pg_sessionmaker):
    uid = await _user(pg_sessionmaker, 9006)
    mid = await _make(pg_sessionmaker, code_owner=uid, status="pending")
    async with pg_sessionmaker() as s:
        media = await MediaService(s).reject(mid, admin_id=42, note="نامناسب")
        assert media is not None
    async with pg_sessionmaker() as s:
        m = await s.get(Media, mid)
        assert m.status == "rejected" and m.review_note == "نامناسب"


async def test_approve_reject_only_pending(pg_sessionmaker):
    uid = await _user(pg_sessionmaker, 9007)
    mid = await _make(pg_sessionmaker, code_owner=uid, status="approved")
    async with pg_sessionmaker() as s:
        svc = MediaService(s)
        assert await svc.approve(mid, admin_id=1) is None  # already approved
        assert await svc.reject(mid, admin_id=1, note="x") is None


# --- quota + helpers --------------------------------------------------------
async def test_quota_counts_exclude_rejected(pg_sessionmaker):
    uid = await _user(pg_sessionmaker, 9008)
    await _make(pg_sessionmaker, code_owner=uid, status="approved")
    await _make(pg_sessionmaker, code_owner=uid, status="pending")
    await _make(pg_sessionmaker, code_owner=uid, status="rejected")
    async with pg_sessionmaker() as s:
        svc = MediaService(s)
        assert await svc.count_quota_by_owner(uid) == 2  # approved + pending
        assert await svc.count_by_owner(uid) == 3  # all
        assert await svc.count_pending() == 1
        assert await svc.owner_telegram_id(uid) == 9008
