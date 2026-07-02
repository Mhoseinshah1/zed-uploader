"""C2 integration (REAL Postgres): ads around delivery — placement order,
impression counting, and the never-break-delivery guarantee."""
from __future__ import annotations

from unittest.mock import AsyncMock

import app.bot.delivery as delivery
from app.bot.delivery import DeliveryStatus, deliver_by_code
from app.models import Ad, Media, MediaFile, User
from app.services.ad_service import AdService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


class _RecordingBot:
    """Chronological log of ad messages; files are recorded by the patched
    send_media_file into the same list."""

    def __init__(self, events: list):
        self.events = events

    async def send_message(self, chat_id, text, reply_markup=None):
        self.events.append(("ad", text))


async def _seed_media(maker, code="adcode1") -> None:
    async with maker() as s:
        user = User(telegram_id=8801)
        s.add(user)
        media = Media(code=code, status="approved")
        media.files.append(
            MediaFile(sort_order=0, telegram_file_id="f", file_type="document")
        )
        s.add(media)
        await s.commit()


def _patch_file_send(monkeypatch, events: list):
    async def fake_send(bot, chat_id, media_file, **kw):
        events.append(("file", media_file.telegram_file_id))
        return 1

    monkeypatch.setattr(delivery, "send_media_file", fake_send)


async def test_before_and_after_placements_order(pg_sessionmaker, monkeypatch):
    events: list = []
    _patch_file_send(monkeypatch, events)
    await _seed_media(pg_sessionmaker)
    async with pg_sessionmaker() as s:
        await AdService(s).create(title="پیش", text="before!", placement="before_file")
        await AdService(s).create(title="پس", text="after!", placement="after_file")

    async with pg_sessionmaker() as s:
        result = await deliver_by_code(_RecordingBot(events), s, 555, None, "adcode1")
    assert result.status is DeliveryStatus.DELIVERED

    kinds = [k for k, _ in events]
    assert kinds == ["ad", "file", "ad"]  # before -> file -> after
    assert "before!" in events[0][1] and "after!" in events[2][1]

    async with pg_sessionmaker() as s:
        ads = await AdService(s).list_all()
    assert all(a.impression_count == 1 for a in ads)  # both counted once


async def test_delivery_fine_with_no_ads(pg_sessionmaker, monkeypatch):
    events: list = []
    _patch_file_send(monkeypatch, events)
    await _seed_media(pg_sessionmaker, code="adcode2")
    async with pg_sessionmaker() as s:
        result = await deliver_by_code(_RecordingBot(events), s, 555, None, "adcode2")
    assert result.status is DeliveryStatus.DELIVERED
    assert [k for k, _ in events] == ["file"]


async def test_ad_error_never_breaks_delivery(pg_sessionmaker, monkeypatch):
    events: list = []
    _patch_file_send(monkeypatch, events)
    await _seed_media(pg_sessionmaker, code="adcode3")

    async def boom(self, placement, plan):
        raise RuntimeError("ads exploded")

    monkeypatch.setattr(AdService, "pick_for_placement", boom)
    async with pg_sessionmaker() as s:
        result = await deliver_by_code(_RecordingBot(events), s, 555, None, "adcode3")
    assert result.status is DeliveryStatus.DELIVERED  # file still delivered
    assert [k for k, _ in events] == ["file"]


async def test_target_plan_respected_in_delivery(pg_sessionmaker, monkeypatch):
    events: list = []
    _patch_file_send(monkeypatch, events)
    await _seed_media(pg_sessionmaker, code="adcode4")
    async with pg_sessionmaker() as s:
        # the viewer (telegram 555) has no user row -> treated as "free"
        await AdService(s).create(
            title="فری", text="free-only", placement="before_file", target_plan="free"
        )
        await AdService(s).create(
            title="پلاس", text="plus-only", placement="before_file", target_plan="plus"
        )

    async with pg_sessionmaker() as s:
        result = await deliver_by_code(_RecordingBot(events), s, 555, None, "adcode4")
    assert result.status is DeliveryStatus.DELIVERED
    ad_texts = [t for k, t in events if k == "ad"]
    assert len(ad_texts) == 1 and "free-only" in ad_texts[0]
