"""Phase 2 tests — authz, force-join membership, batch build, broadcast guard.

Uses in-memory SQLite (aiosqlite); the bot is mocked. No live DB/Redis/network.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.filters import IsAdmin, IsOwner
from app.models import Admin, Base
from app.services.admin_service import AdminService
from app.services.channel_service import ChannelService
from app.services.media_service import MediaService
from app.services.membership import unjoined_channels

# conftest sets ADMIN_IDS="111,222" -> env owners.
ENV_OWNER = 111


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


# --------------------------------------------------------------------------
# authz
# --------------------------------------------------------------------------
async def _authz() -> None:
    engine, Session = await _make_session()
    async with Session() as s:
        s.add_all(
            [
                Admin(telegram_id=333, role="admin", is_active=True),
                Admin(telegram_id=444, role="owner", is_active=True),
                Admin(telegram_id=555, role="admin", is_active=False),
            ]
        )
        await s.commit()

        # env id is both admin and owner
        assert await AdminService.is_admin(s, ENV_OWNER) is True
        assert await AdminService.is_owner(s, ENV_OWNER) is True
        assert AdminService.is_env_owner(ENV_OWNER) is True

        # db admin: admin yes, owner no
        assert await AdminService.is_admin(s, 333) is True
        assert await AdminService.is_owner(s, 333) is False

        # db owner: both
        assert await AdminService.is_admin(s, 444) is True
        assert await AdminService.is_owner(s, 444) is True

        # inactive admin: neither
        assert await AdminService.is_admin(s, 555) is False
        assert await AdminService.is_owner(s, 555) is False

        # unknown: neither
        assert await AdminService.is_admin(s, 999) is False
        assert await AdminService.is_owner(s, 999) is False
    await engine.dispose()


def test_authz_logic():
    asyncio.run(_authz())


# --------------------------------------------------------------------------
# force-join membership (mock bot)
# --------------------------------------------------------------------------
class _FakeBot:
    def __init__(self, mapping):
        self.mapping = mapping

    async def get_chat_member(self, chat_id, user_id):
        value = self.mapping[chat_id]
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(status=value)


async def _membership() -> None:
    engine, Session = await _make_session()
    async with Session() as s:
        svc = ChannelService(s)
        await svc.add("@joined")
        await svc.add("@left")
        await svc.add("@broken")
        bot = _FakeBot(
            {"@joined": "member", "@left": "left", "@broken": RuntimeError("no access")}
        )
        result = await unjoined_channels(bot, s, user_id=42)
        chat_ids = [c.chat_id for c in result]
        # only "left"/"kicked" count; the erroring channel fails open (skipped)
        assert chat_ids == ["@left"]
    await engine.dispose()


def test_unjoined_channels():
    asyncio.run(_membership())


# --------------------------------------------------------------------------
# batch finalize builds ONE Media with N MediaFiles in order
# --------------------------------------------------------------------------
async def _batch_build() -> None:
    engine, Session = await _make_session()
    async with Session() as s:
        files = [
            {"telegram_file_id": "A", "file_type": "photo"},
            {"telegram_file_id": "B", "file_type": "video"},
            {"telegram_file_id": "C", "file_type": "document"},
        ]
        media = await MediaService(s).create_media(files=files, owner_user_id=None)
        assert len(media.files) == 3
        ordered = sorted(media.files, key=lambda f: f.sort_order)
        assert [f.sort_order for f in ordered] == [0, 1, 2]
        assert [f.telegram_file_id for f in ordered] == ["A", "B", "C"]
    await engine.dispose()


def test_batch_finalize_builds_one_media():
    asyncio.run(_batch_build())


# --------------------------------------------------------------------------
# broadcast is owner-gated: a non-owner fails the IsOwner filter
# --------------------------------------------------------------------------
async def _broadcast_guard() -> None:
    engine, Session = await _make_session()
    async with Session() as s:
        non_owner = SimpleNamespace(from_user=SimpleNamespace(id=999))
        owner = SimpleNamespace(from_user=SimpleNamespace(id=ENV_OWNER))
        assert await IsOwner()(non_owner, s) is False
        assert await IsAdmin()(non_owner, s) is False
        assert await IsOwner()(owner, s) is True
    await engine.dispose()


def test_broadcast_owner_guard():
    asyncio.run(_broadcast_guard())
