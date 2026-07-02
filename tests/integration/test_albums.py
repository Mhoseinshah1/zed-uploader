"""B4 integration (REAL Postgres): worker finalizes buffered albums into ONE
Media, preserving order + first caption, and routing user albums to review."""
from __future__ import annotations

from unittest.mock import AsyncMock

import fakeredis.aioredis as fakeredis
from sqlalchemy import select

import app.workers.main as worker
from app.models import Media, User
from app.services.album_buffer import AlbumBuffer
from app.services.bot_setting_service import (
    KEY_USER_UPLOAD_ENABLED,
    KEY_USER_UPLOAD_REVIEW,
    BotSettingService,
)
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


class _BotProvider:  # Fix-2: wrap a fake bot as a TenantBotProvider
    def __init__(self, bot):
        self._bot = bot
    async def get(self, session, tenant_id):
        return self._bot

ENV_ADMIN = 111  # conftest sets ADMIN_IDS="111,222"


def _part(fid, caption=None):
    return {"file": {"telegram_file_id": fid, "file_type": "photo"}, "caption": caption}


async def _seed(redis, gk, chat_id, telegram_id, parts):
    buf = AlbumBuffer(redis)
    for p in parts:
        await buf.add(gk, tenant_id=1, chat_id=chat_id, telegram_id=telegram_id, part=p, now=0)


async def _media(maker):
    async with maker() as s:
        return list(await s.scalars(select(Media).order_by(Media.id)))


# three parts -> one Media, three files in order, first caption -------------
async def test_three_parts_one_media(pg_sessionmaker):
    redis = fakeredis.FakeRedis(decode_responses=True)
    gk = AlbumBuffer.group_key(1, 500, "G1")
    await _seed(
        redis, gk, 500, ENV_ADMIN,
        [_part("A", "first cap"), _part("B"), _part("C")],
    )
    n = await worker.process_albums_once(_BotProvider(AsyncMock()), redis, pg_sessionmaker)
    assert n == 1

    media = await _media(pg_sessionmaker)
    assert len(media) == 1
    m = media[0]
    assert [f.telegram_file_id for f in m.files] == ["A", "B", "C"]  # arrival order
    assert m.caption == "first cap"
    assert m.status == "approved"  # admin upload


# two interleaved albums do not mix -----------------------------------------
async def test_two_albums_do_not_mix(pg_sessionmaker):
    redis = fakeredis.FakeRedis(decode_responses=True)
    g1 = AlbumBuffer.group_key(1, 500, "G1")
    g2 = AlbumBuffer.group_key(1, 500, "G2")
    buf = AlbumBuffer(redis)
    await buf.add(g1, tenant_id=1, chat_id=500, telegram_id=ENV_ADMIN, part=_part("A"), now=0)
    await buf.add(g2, tenant_id=1, chat_id=500, telegram_id=ENV_ADMIN, part=_part("X"), now=0)
    await buf.add(g1, tenant_id=1, chat_id=500, telegram_id=ENV_ADMIN, part=_part("B"), now=0)
    await buf.add(g2, tenant_id=1, chat_id=500, telegram_id=ENV_ADMIN, part=_part("Y"), now=0)

    n = await worker.process_albums_once(_BotProvider(AsyncMock()), redis, pg_sessionmaker)
    assert n == 2

    media = await _media(pg_sessionmaker)
    file_sets = sorted(
        sorted(f.telegram_file_id for f in m.files) for m in media
    )
    assert file_sets == [["A", "B"], ["X", "Y"]]


# a normal user's album routes to review (pending) when review is on --------
async def test_user_album_goes_to_review(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        s.add(User(telegram_id=5001))
        setting = BotSettingService(s)
        await setting.set(KEY_USER_UPLOAD_ENABLED, True)
        await setting.set(KEY_USER_UPLOAD_REVIEW, True)
        await s.commit()

    redis = fakeredis.FakeRedis(decode_responses=True)
    gk = AlbumBuffer.group_key(1, 600, "GU")
    await _seed(redis, gk, 600, 5001, [_part("U1", "hi"), _part("U2")])

    n = await worker.process_albums_once(_BotProvider(AsyncMock()), redis, pg_sessionmaker)
    assert n == 1

    media = await _media(pg_sessionmaker)
    assert len(media) == 1
    m = media[0]
    assert m.status == "pending"  # not deliverable until an admin approves
    assert [f.telegram_file_id for f in m.files] == ["U1", "U2"]
    async with pg_sessionmaker() as s:
        user = await s.scalar(select(User).where(User.telegram_id == 5001))
    assert m.owner_user_id == user.id


# uploads disabled -> a non-admin album is not created ----------------------
async def test_user_album_blocked_when_uploads_off(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        s.add(User(telegram_id=5002))
        await s.commit()
    redis = fakeredis.FakeRedis(decode_responses=True)
    gk = AlbumBuffer.group_key(1, 700, "GX")
    await _seed(redis, gk, 700, 5002, [_part("Z1"), _part("Z2")])

    await worker.process_albums_once(_BotProvider(AsyncMock()), redis, pg_sessionmaker)
    assert await _media(pg_sessionmaker) == []  # nothing created
