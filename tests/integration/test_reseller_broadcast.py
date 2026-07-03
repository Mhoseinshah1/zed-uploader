"""H3 integration (REAL Postgres) — the reseller broadcast is exactly-once.

create_job_for_users snapshots the given owners as a ledger; the same worker
that drains any broadcast delivers each owner exactly once and never re-sends
on a re-run.
"""
from __future__ import annotations

from sqlalchemy import select

import app.workers.main as worker
from app.core.tenant_context import tenant_scope
from app.models import BroadcastRecipient, User
from app.services import broadcast as bcast
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


class _FakeBot:
    def __init__(self):
        self.sent: list[int] = []

    async def send_message(self, chat_id, text):
        self.sent.append(chat_id)


class _BotProvider:
    def __init__(self, bot):
        self._bot = bot

    async def get(self, session, tenant_id):
        return self._bot


async def _drain(bot, maker):
    while await worker.process_broadcast_once(_BotProvider(bot), maker):
        pass


async def _owner_rows(maker, telegram_ids):
    rows = []
    with tenant_scope(1):
        async with maker() as s:
            for tg in telegram_ids:
                u = User(telegram_id=tg)
                s.add(u)
                await s.flush()
                rows.append((u.id, tg))
            await s.commit()
    return rows


async def test_reseller_broadcast_delivers_each_owner_once(pg_sessionmaker):
    rows = await _owner_rows(pg_sessionmaker, [7101, 7102, 7103])
    # duplicate an owner (someone owning two bots) — must still get ONE message
    rows_with_dupe = rows + [rows[0]]
    with tenant_scope(1):
        async with pg_sessionmaker() as s:
            job = await bcast.create_job_for_users(
                s, user_rows=rows_with_dupe, text="سلام نماینده", created_by=None
            )
            job_id = job.id
    assert job.total == 3  # deduped

    bot = _FakeBot()
    await _drain(bot, pg_sessionmaker)
    assert sorted(bot.sent) == [7101, 7102, 7103]  # each owner exactly once

    # a re-run sends nothing more (exactly-once ledger)
    bot2 = _FakeBot()
    await _drain(bot2, pg_sessionmaker)
    assert bot2.sent == []

    with tenant_scope(1):
        async with pg_sessionmaker() as s:
            statuses = set(
                (await s.scalars(
                    select(BroadcastRecipient.status).where(
                        BroadcastRecipient.broadcast_id == job_id
                    )
                )).all()
            )
    assert statuses == {"sent"}
