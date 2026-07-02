"""A3 integration — broadcast exactly-once ledger (REAL Postgres).

Covers the parts where constraints/exact-once actually matter: the per-recipient
snapshot + UNIQUE, the worker moving rows off `pending` once, a re-run never
re-sending `sent` rows, and retry re-processing only `failed`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import app.workers.main as worker
from app.models import BroadcastJob, BroadcastRecipient, User
from app.services import broadcast as bcast
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


class _FakeBot:
    """copy_message succeeds unless the target is in `forbidden`/`fail`."""

    def __init__(self, *, forbidden=(), fail=()):
        self.forbidden = set(forbidden)
        self.fail = set(fail)
        self.copied: list[int] = []
        self.summaries: list[int] = []

    async def copy_message(self, chat_id, from_chat_id, message_id):
        if chat_id in self.forbidden:
            raise TelegramForbiddenError(method=SimpleNamespace(), message="blocked")
        if chat_id in self.fail:
            raise RuntimeError("transient boom")
        self.copied.append(chat_id)

    async def send_message(self, chat_id, text):
        self.summaries.append(chat_id)


async def _seed_users(maker, telegram_ids) -> dict[int, int]:
    """Create users; return {telegram_id: user_id}."""
    mapping = {}
    async with maker() as s:
        for tg in telegram_ids:
            u = User(telegram_id=tg)
            s.add(u)
            await s.flush()
            mapping[tg] = u.id
        await s.commit()
    return mapping


class _BotProvider:  # Fix-2: wrap a fake bot as a TenantBotProvider
    def __init__(self, bot):
        self._bot = bot
    async def get(self, session, tenant_id):
        return self._bot


async def _drain(bot, maker) -> None:
    while await worker.process_broadcast_once(_BotProvider(bot), maker):
        pass


async def _recipient_statuses(maker, job_id) -> dict[int, str]:
    async with maker() as s:
        rows = list(
            await s.scalars(
                select(BroadcastRecipient).where(
                    BroadcastRecipient.broadcast_id == job_id
                )
            )
        )
    return {r.telegram_id: r.status for r in rows}


# snapshot: one pending row per user + UNIQUE(broadcast_id, user_id) ----------
async def test_snapshot_one_row_per_user_and_unique(pg_sessionmaker):
    ids = await _seed_users(pg_sessionmaker, [8001, 8002, 8003])
    async with pg_sessionmaker() as s:
        job = await bcast.create_job(s, from_chat_id=1, message_id=10, created_by=999)
        job_id = job.id

    async with pg_sessionmaker() as s:
        count = int(
            await s.scalar(
                select(func.count(BroadcastRecipient.id)).where(
                    BroadcastRecipient.broadcast_id == job_id
                )
            )
        )
        job = await s.get(BroadcastJob, job_id)
    assert count == 3 and job.total == 3
    assert all(v == "pending" for v in (await _recipient_statuses(pg_sessionmaker, job_id)).values())

    # a duplicate (broadcast_id, user_id) is rejected by the UNIQUE constraint
    with pytest.raises(IntegrityError):
        async with pg_sessionmaker() as s:
            s.add(
                BroadcastRecipient(
                    broadcast_id=job_id, user_id=ids[8001], telegram_id=8001, status="pending"
                )
            )
            await s.commit()


# worker marks sent / failed / blocked correctly ----------------------------
async def test_worker_marks_statuses(pg_sessionmaker):
    ids = await _seed_users(pg_sessionmaker, [8101, 8102, 8103])
    async with pg_sessionmaker() as s:
        job = await bcast.create_job(s, from_chat_id=1, message_id=10, created_by=999)
        job_id = job.id

    bot = _FakeBot(forbidden={8102}, fail={8103})
    await _drain(bot, pg_sessionmaker)

    statuses = await _recipient_statuses(pg_sessionmaker, job_id)
    assert statuses == {8101: "sent", 8102: "blocked", 8103: "failed"}
    assert sorted(bot.copied) == [8101]

    async with pg_sessionmaker() as s:
        job = await s.get(BroadcastJob, job_id)
        blocked_user = await s.get(User, ids[8102])
        failed_row = await s.scalar(
            select(BroadcastRecipient).where(
                BroadcastRecipient.broadcast_id == job_id,
                BroadcastRecipient.telegram_id == 8103,
            )
        )
    assert job.status == "done"
    assert job.sent == 1 and job.blocked == 1 and job.failed == 1
    assert blocked_user.is_blocked is True
    assert failed_row.error_message  # transient error recorded


# a re-run over a finished job does NOT re-send sent rows --------------------
async def test_rerun_does_not_resend(pg_sessionmaker):
    await _seed_users(pg_sessionmaker, [8201, 8202])
    async with pg_sessionmaker() as s:
        job = await bcast.create_job(s, from_chat_id=1, message_id=10, created_by=999)
        job_id = job.id

    bot = _FakeBot()
    await _drain(bot, pg_sessionmaker)
    assert sorted(bot.copied) == [8201, 8202]

    # everything is done; another drain sends nothing more
    bot2 = _FakeBot()
    await _drain(bot2, pg_sessionmaker)
    assert bot2.copied == []
    async with pg_sessionmaker() as s:
        job = await s.get(BroadcastJob, job_id)
    assert job.sent == 2


# retry re-processes ONLY failed rows ---------------------------------------
async def test_retry_only_failed(pg_sessionmaker):
    await _seed_users(pg_sessionmaker, [8301, 8302, 8303])
    async with pg_sessionmaker() as s:
        job = await bcast.create_job(s, from_chat_id=1, message_id=10, created_by=999)
        job_id = job.id

    # first pass: 8302 fails
    await _drain(_FakeBot(fail={8302}), pg_sessionmaker)
    assert (await _recipient_statuses(pg_sessionmaker, job_id))[8302] == "failed"

    async with pg_sessionmaker() as s:
        requeued = await bcast.retry_failed(s, job_id)
    assert requeued == 1

    # second pass with a healthy bot: only 8302 is re-attempted
    bot = _FakeBot()
    await _drain(bot, pg_sessionmaker)
    assert bot.copied == [8302]  # 8301/8303 already sent, not resent

    statuses = await _recipient_statuses(pg_sessionmaker, job_id)
    assert statuses == {8301: "sent", 8302: "sent", 8303: "sent"}
    async with pg_sessionmaker() as s:
        job = await s.get(BroadcastJob, job_id)
    assert job.status == "done" and job.sent == 3 and job.failed == 0


# panel job listing is newest-first with populated fields --------------------
async def test_list_jobs_newest_first(pg_sessionmaker):
    await _seed_users(pg_sessionmaker, [8401])
    async with pg_sessionmaker() as s:
        first = await bcast.create_job(s, text="one", created_by=None)
    async with pg_sessionmaker() as s:
        second = await bcast.create_job(s, text="two", created_by=None)

    async with pg_sessionmaker() as s:
        jobs = await bcast.list_jobs(s, limit=10)
    assert [j.id for j in jobs][:2] == [second.id, first.id]
    assert jobs[0].total == 1 and jobs[0].created_at is not None
