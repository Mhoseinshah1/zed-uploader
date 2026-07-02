"""Fix-2 (REAL Postgres) — background workers carry the tenant and act with the
RIGHT tenant's bot: autodelete round-trips tenant, broadcast for tenant A sends
only via A's bot, and a missing/suspended tenant bot degrades gracefully."""
from __future__ import annotations

from unittest.mock import AsyncMock

import fakeredis.aioredis as fakeredis
from sqlalchemy import select

import app.workers.main as worker
from app.core.tenant_context import all_tenants, tenant_scope
from app.models.broadcast import BroadcastJob
from app.models.tenant import Tenant
from app.models.user import User
from app.services import broadcast as bcast
from app.services.autodelete import AutoDeleteQueue
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


class _Bot:
    def __init__(self):
        self.deleted = []
        self.copied = []
        self.sent = []

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    async def copy_message(self, chat_id, from_chat_id, message_id):
        self.copied.append(chat_id)

    async def send_message(self, chat_id, text):
        self.sent.append(chat_id)


class _MultiProvider:
    """Registry stand-in: a distinct fake bot per tenant; None => unavailable."""

    def __init__(self, bots: dict[int, object]):
        self.bots = bots

    async def get(self, session, tenant_id):
        return self.bots.get(tenant_id)


async def _two_customer_tenants(maker):
    with all_tenants():
        async with maker() as s:
            a = Tenant(bot_username="A", bot_id=1, status="active")
            b = Tenant(bot_username="B", bot_id=2, status="active")
            s.add_all([a, b])
            await s.commit()
            return a.id, b.id


async def test_autodelete_uses_the_items_tenant_bot(pg_sessionmaker):
    a, b = await _two_customer_tenants(pg_sessionmaker)
    redis = fakeredis.FakeRedis(decode_responses=True)
    q = AutoDeleteQueue(redis)
    # schedule a delete for each tenant (due immediately: seconds small, then wait)
    await q.schedule(1000, [11], seconds=1, tenant_id=a)
    await q.schedule(2000, [22], seconds=1, tenant_id=b)
    import time as _t

    # force them due by rewriting scores to the past
    await redis.zadd("autodelete:queue", {m: 0 for m in await redis.zrange("autodelete:queue", 0, -1)})

    bot_a, bot_b = _Bot(), _Bot()
    provider = _MultiProvider({a: bot_a, b: bot_b})
    n = await worker.process_once(provider, pg_sessionmaker, q)
    assert n == 2
    # each delete went to its OWN tenant's bot, never the other
    assert bot_a.deleted == [(1000, 11)] and bot_b.deleted == [(2000, 22)]


async def test_autodelete_missing_bot_is_dropped(pg_sessionmaker):
    a, b = await _two_customer_tenants(pg_sessionmaker)
    redis = fakeredis.FakeRedis(decode_responses=True)
    q = AutoDeleteQueue(redis)
    await q.schedule(3000, [33], seconds=1, tenant_id=a)
    await redis.zadd("autodelete:queue", {m: 0 for m in await redis.zrange("autodelete:queue", 0, -1)})
    # provider has NO bot for tenant a (suspended/gone) -> drop, no crash
    n = await worker.process_once(_MultiProvider({}), pg_sessionmaker, q)
    assert n == 1  # acked (removed) without deleting
    assert await redis.zcard("autodelete:queue") == 0


async def test_broadcast_sends_only_via_its_tenants_bot(pg_sessionmaker):
    a, b = await _two_customer_tenants(pg_sessionmaker)
    # a user + a broadcast job in EACH tenant
    with tenant_scope(a):
        async with pg_sessionmaker() as s:
            s.add(User(telegram_id=101))
            await s.commit()
            await bcast.create_job(s, from_chat_id=1, message_id=1, created_by=None)
    with tenant_scope(b):
        async with pg_sessionmaker() as s:
            s.add(User(telegram_id=202))
            await s.commit()
            await bcast.create_job(s, from_chat_id=1, message_id=1, created_by=None)

    bot_a, bot_b = _Bot(), _Bot()
    provider = _MultiProvider({a: bot_a, b: bot_b})
    # drain everything
    for _ in range(20):
        if not await worker.process_broadcast_once(provider, pg_sessionmaker):
            break
    # tenant A's recipient reached only via A's bot; B's only via B's bot
    assert bot_a.copied == [101] and bot_b.copied == [202]


async def test_broadcast_missing_bot_fails_job_gracefully(pg_sessionmaker):
    a, _ = await _two_customer_tenants(pg_sessionmaker)
    with tenant_scope(a):
        async with pg_sessionmaker() as s:
            s.add(User(telegram_id=303))
            await s.commit()
            job = await bcast.create_job(s, from_chat_id=1, message_id=1, created_by=None)
            job_id = job.id
    # no bot for tenant a -> the job is failed (not looped forever), no crash
    await worker.process_broadcast_once(_MultiProvider({}), pg_sessionmaker)
    with tenant_scope(a):
        async with pg_sessionmaker() as s:
            done = await s.get(BroadcastJob, job_id)
    assert done.status == "failed"
