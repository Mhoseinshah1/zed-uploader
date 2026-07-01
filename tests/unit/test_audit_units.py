"""Unit-tier audit tests (items 9-13).

DB-touching cases use in-memory SQLite (no money locking here, so SQLite is
acceptable per the audit); Redis/bot are faked/mocked.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest_asyncio
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.workers.main as worker
from app.bot.filters import IsAdmin, IsOwner
from app.models import (
    Admin,
    Base,
    BroadcastJob,
    BroadcastRecipient,
    FeatureFlag,
    Media,
    User,
)
from app.services import broadcast as bcast
from app.services.admin_service import AdminService
from app.services.channel_service import ChannelService
from app.services.feature_service import FeatureService
from app.services.media_service import MediaService
from app.services.membership import unjoined_channels

ENV_OWNER = 111  # conftest sets ADMIN_IDS="111,222"


@pytest_asyncio.fixture
async def sqlite_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# 9 -------------------------------------------------------------------------
async def test_feature_gating_and_expiry(sqlite_maker):
    async with sqlite_maker() as s:
        s.add(FeatureFlag(key="protect_content", is_enabled=True, plan="plus"))
        free = User(telegram_id=1, plan="free")
        plus = User(
            telegram_id=2, plan="plus",
            plan_expires_at=datetime.now(timezone.utc) + timedelta(days=3),
        )
        expired = User(
            telegram_id=3, plan="plus",
            plan_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        s.add_all([free, plus, expired])
        await s.commit()
        assert await FeatureService.is_enabled(s, "protect_content", free) is False
        assert await FeatureService.is_enabled(s, "protect_content", plus) is True
        # expired plus resolves effective_plan="free" -> gated
        assert expired.effective_plan == "free"
        assert await FeatureService.is_enabled(s, "protect_content", expired) is False


# 10 ------------------------------------------------------------------------
async def test_owner_admin_resolution(sqlite_maker):
    async with sqlite_maker() as s:
        s.add_all([
            Admin(telegram_id=333, role="admin", is_active=True),
            Admin(telegram_id=444, role="admin", is_active=False),
            Admin(telegram_id=555, role="owner", is_active=True),
        ])
        await s.commit()
        assert await AdminService.is_owner(s, ENV_OWNER) is True
        assert await AdminService.is_admin(s, ENV_OWNER) is True
        assert await AdminService.is_admin(s, 333) is True
        assert await AdminService.is_owner(s, 333) is False
        assert await AdminService.is_admin(s, 444) is False  # inactive
        assert await AdminService.is_owner(s, 555) is True
        assert await AdminService.is_admin(s, 999) is False


# 11 ------------------------------------------------------------------------
class _FakeBot:
    def __init__(self, mapping):
        self.mapping = mapping

    async def get_chat_member(self, chat_id, user_id):
        value = self.mapping[chat_id]
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(status=value)


async def test_unjoined_channels_filters_and_fails_open(sqlite_maker):
    async with sqlite_maker() as s:
        svc = ChannelService(s)
        await svc.add("@member")
        await svc.add("@left")
        await svc.add("@kicked")
        await svc.add("@broken")
        bot = _FakeBot({
            "@member": "member", "@left": "left", "@kicked": "kicked",
            "@broken": RuntimeError("bot not admin"),
        })
        result = await unjoined_channels(bot, s, user_id=7)
        chat_ids = sorted(c.chat_id for c in result)
    assert chat_ids == ["@kicked", "@left"]  # broken is skipped (fail-open)


# 12 ------------------------------------------------------------------------
async def test_batch_finalize_one_media_ordered(sqlite_maker):
    async with sqlite_maker() as s:
        buffered = [
            {"file": {"telegram_file_id": "A", "file_type": "photo"}, "caption": "cap"},
            {"file": {"telegram_file_id": "B", "file_type": "video"}, "caption": None},
            {"file": {"telegram_file_id": "C", "file_type": "document"}, "caption": None},
        ]
        files = [it["file"] for it in buffered]
        caption = buffered[0]["caption"]
        media = await MediaService(s).create_media(files=files, caption=caption)
        ordered = sorted(media.files, key=lambda f: f.sort_order)
        assert len(ordered) == 1 * len(files)
        assert [f.sort_order for f in ordered] == [0, 1, 2]
        assert [f.telegram_file_id for f in ordered] == ["A", "B", "C"]
        assert media.caption == "cap"


# 13a broadcast owner guard -------------------------------------------------
async def test_broadcast_owner_guard(sqlite_maker):
    async with sqlite_maker() as s:
        non_owner = SimpleNamespace(from_user=SimpleNamespace(id=999))
        owner = SimpleNamespace(from_user=SimpleNamespace(id=ENV_OWNER))
        assert await IsOwner()(non_owner, s) is False
        assert await IsAdmin()(non_owner, s) is False
        assert await IsOwner()(owner, s) is True


# 13b broadcast worker marks blocked + records per-recipient statuses --------
class _BroadcastBot:
    def __init__(self, blocked_telegram_id):
        self.blocked = blocked_telegram_id
        self.copied = []       # copy_message targets that succeeded
        self.summaries = []    # send_message (job completion summary) targets

    async def copy_message(self, chat_id, from_chat_id, message_id):
        if chat_id == self.blocked:
            raise TelegramForbiddenError(method=SimpleNamespace(), message="blocked")
        self.copied.append(chat_id)

    async def send_message(self, chat_id, text):  # completion summary only here
        self.summaries.append(chat_id)


async def test_broadcast_worker_marks_blocked_and_advances(sqlite_maker):
    async with sqlite_maker() as s:
        u1 = User(telegram_id=7001)
        u2 = User(telegram_id=7002)  # will "block" the bot
        u3 = User(telegram_id=7003)
        s.add_all([u1, u2, u3])
        await s.commit()
        ids = (u1.id, u2.id, u3.id)
        job = await bcast.create_job(s, from_chat_id=1, message_id=10, created_by=555)
        job_id = job.id

    bot = _BroadcastBot(blocked_telegram_id=7002)
    # drain the job to completion (page -> finalize -> idle)
    assert await worker.process_broadcast_once(bot, sqlite_maker) is True
    while await worker.process_broadcast_once(bot, sqlite_maker):
        pass

    async with sqlite_maker() as s:
        blocked = await s.get(User, ids[1])
        others = [await s.get(User, ids[0]), await s.get(User, ids[2])]
        done = await s.get(BroadcastJob, job_id)
        recips = list(
            await s.scalars(
                select(BroadcastRecipient).where(
                    BroadcastRecipient.broadcast_id == job_id
                )
            )
        )
    assert blocked.is_blocked is True
    assert all(u.is_blocked is False for u in others)
    assert done.status == "done"
    assert done.sent == 2 and done.blocked == 1 and done.failed == 0
    assert sorted(bot.copied) == [7001, 7003]  # blocked user never delivered
    assert all(r.status != "pending" for r in recips)  # ledger fully drained

    # exactly-once: another pass must not re-send anything
    before = list(bot.copied)
    await worker.process_broadcast_once(bot, sqlite_maker)
    assert bot.copied == before
