"""G1 tests — per-tenant Telegram topic logger (mock Telegram; SQLite).

Topic auto-creation + storage, routing to the right tenant's group+topic, card
redaction, cross-tenant isolation, and a silent no-op when unconfigured.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Base, Tenant
from app.services.tenant_logger import TenantLogger, mask_card


class FakeBot:
    def __init__(self):
        self._next = 100
        self.topics_created = []  # (chat_id, name)
        self.sent = []           # dicts of send_message kwargs
        self.session = SimpleNamespace(close=AsyncMock())

    async def create_forum_topic(self, chat_id, name):
        self._next += 1
        self.topics_created.append((chat_id, name))
        return SimpleNamespace(message_thread_id=self._next)

    async def send_message(self, chat_id, text, message_thread_id=None):
        self.sent.append(
            {"chat_id": chat_id, "text": text, "thread": message_thread_id}
        )


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _tenant(maker, group_id) -> int:
    with all_tenants():
        async with maker() as s:
            t = Tenant(bot_username="c", status="active")
            s.add(t)
            await s.commit()
            tid = t.id
    with tenant_scope(tid):
        async with maker() as s:
            await TenantLogger(s).set_group(group_id)
    return tid


def test_mask_card():
    assert mask_card("6037991122334455") == "6037••••4455"
    assert mask_card("6037-9911-2233-4455") == "6037••••4455"
    assert mask_card(None) == "?"
    assert "9911" not in mask_card("6037991122334455")  # middle never shown


async def test_topic_auto_created_stored_and_reused(maker):
    tid = await _tenant(maker, group_id=-1001)
    bot = FakeBot()
    with tenant_scope(tid):
        async with maker() as s:
            await TenantLogger(s).emit("payments", "x", bot=bot)
        # thread id persisted on the tenant's settings row
        async with maker() as s:
            row = await TenantLogger(s).get_settings()
            thread = row.topic_payments
        assert thread is not None
        assert bot.topics_created == [(-1001, "پرداخت‌ها")]
        assert bot.sent[0]["chat_id"] == -1001 and bot.sent[0]["thread"] == thread
        # a second event reuses the topic (no new create)
        async with maker() as s:
            await TenantLogger(s).emit("payments", "y", bot=bot)
    assert len(bot.topics_created) == 1 and len(bot.sent) == 2


async def test_events_route_to_their_topic(maker):
    tid = await _tenant(maker, group_id=-1002)
    bot = FakeBot()
    with tenant_scope(tid):
        async with maker() as s:
            lg = TenantLogger(s)
            await lg.log_upload("ABC", 55, bot=bot)
            await lg.log_new_user(55, "Ali", bot=bot)
    names = {c[1] for c in bot.topics_created}
    assert names == {"آپلودها", "کاربران جدید"}
    threads = {m["thread"] for m in bot.sent}
    assert len(threads) == 2  # different topics -> different thread ids


async def test_payment_redaction(maker):
    tid = await _tenant(maker, group_id=-1003)
    bot = FakeBot()
    with tenant_scope(tid):
        async with maker() as s:
            await TenantLogger(s).log_payment(
                method="card", amount=50000, card="6037991122334455",
                ref="RCP-1", bot=bot,
            )
    text = bot.sent[0]["text"]
    assert "6037••••4455" in text and "6037991122334455" not in text


async def test_cross_tenant_isolation(maker):
    t2 = await _tenant(maker, group_id=-2000)
    t3 = await _tenant(maker, group_id=-3000)
    bot2, bot3 = FakeBot(), FakeBot()
    with tenant_scope(t2):
        async with maker() as s:
            await TenantLogger(s).emit("payments", "for t2", bot=bot2)
    with tenant_scope(t3):
        async with maker() as s:
            await TenantLogger(s).emit("payments", "for t3", bot=bot3)
    # each event went ONLY to its own tenant's group
    assert [m["chat_id"] for m in bot2.sent] == [-2000]
    assert [m["chat_id"] for m in bot3.sent] == [-3000]


async def test_no_op_when_unconfigured(maker):
    # tenant with no log settings row at all
    with all_tenants():
        async with maker() as s:
            t = Tenant(bot_username="none", status="active")
            s.add(t)
            await s.commit()
            tid = t.id
    bot = FakeBot()
    with tenant_scope(tid):
        async with maker() as s:
            assert await TenantLogger(s).emit("payments", "x", bot=bot) is False
    assert bot.sent == [] and bot.topics_created == []
