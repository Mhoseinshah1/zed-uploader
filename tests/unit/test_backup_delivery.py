"""G2 tests — DB backup delivered to the tenant's backup topic (mock Telegram).

A configured tenant gets its dump sent to its own backup topic; an oversized
dump is gzipped or replaced with a "download from panel" notice; a send failure
never raises; delivery goes only to the configured tenant's group.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.services.tenant_logger as tl
from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Base, Tenant
from app.services.tenant_logger import TenantLogger


class FakeBot:
    def __init__(self):
        self._next = 200
        self.docs = []      # (chat_id, thread, caption)
        self.messages = []  # (chat_id, text)
        self.session = SimpleNamespace(close=AsyncMock())

    async def create_forum_topic(self, chat_id, name):
        self._next += 1
        return SimpleNamespace(message_thread_id=self._next)

    async def send_document(self, chat_id, document, caption=None, message_thread_id=None):
        self.docs.append((chat_id, message_thread_id, caption))

    async def send_message(self, chat_id, text, message_thread_id=None):
        self.messages.append((chat_id, text))


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


async def test_backup_sent_to_backup_topic(maker, tmp_path):
    tid = await _tenant(maker, -1500)
    dump = tmp_path / "dump.sql"
    dump.write_bytes(b"-- small dump\n" * 100)
    bot = FakeBot()
    with tenant_scope(tid):
        async with maker() as s:
            ok = await TenantLogger(s).deliver_backup(
                str(dump), dump.stat().st_size, bot=bot
            )
    assert ok is True
    assert len(bot.docs) == 1 and bot.docs[0][0] == -1500  # tenant's group
    assert "بکاپ" in bot.docs[0][2]  # caption present


async def test_oversized_dump_is_gzipped(maker, tmp_path, monkeypatch):
    # tiny limit so a small file counts as "oversized" but gzips under it
    monkeypatch.setattr(tl, "TELEGRAM_UPLOAD_LIMIT", 50)
    tid = await _tenant(maker, -1501)
    dump = tmp_path / "big.sql"
    dump.write_bytes(b"A" * 500)  # 500 raw bytes; gzips to well under 50
    bot = FakeBot()
    with tenant_scope(tid):
        async with maker() as s:
            ok = await TenantLogger(s).deliver_backup(str(dump), 500, bot=bot)
    assert ok is True
    assert "فشرده" in bot.docs[0][2]  # compressed marker in caption


async def test_too_large_sends_notice_not_file(maker, tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "TELEGRAM_UPLOAD_LIMIT", 5)
    tid = await _tenant(maker, -1502)
    dump = tmp_path / "huge.sql"
    dump.write_bytes(bytes(10_000))  # zeros gzip small... use random-ish incompressible
    dump.write_bytes(__import__("os").urandom(10_000))
    bot = FakeBot()
    with tenant_scope(tid):
        async with maker() as s:
            ok = await TenantLogger(s).deliver_backup(str(dump), 10_000, bot=bot)
    assert ok is False
    assert bot.docs == []  # no file sent
    assert any("پنل" in m[1] for m in bot.messages)  # a "download from panel" notice


async def test_send_failure_does_not_raise(maker, tmp_path):
    tid = await _tenant(maker, -1503)
    dump = tmp_path / "d.sql"
    dump.write_bytes(b"x")
    bot = FakeBot()
    bot.send_document = AsyncMock(side_effect=RuntimeError("telegram down"))
    with tenant_scope(tid):
        async with maker() as s:
            ok = await TenantLogger(s).deliver_backup(str(dump), 1, bot=bot)
    assert ok is False  # swallowed, never raised


async def test_no_group_is_noop(maker, tmp_path):
    with all_tenants():
        async with maker() as s:
            t = Tenant(bot_username="n", status="active")
            s.add(t)
            await s.commit()
            tid = t.id
    dump = tmp_path / "d.sql"
    dump.write_bytes(b"x")
    bot = FakeBot()
    with tenant_scope(tid):
        async with maker() as s:
            assert await TenantLogger(s).deliver_backup(str(dump), 1, bot=bot) is False
    assert bot.docs == []
