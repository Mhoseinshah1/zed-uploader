"""B2 unit tests — FolderService logic + MediaService.set_folder validation.

In-memory SQLite; these cases are app-level logic (no reliance on the DB's
ON DELETE SET NULL, which is covered by the Postgres integration tests).
"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base, User
from app.services.folder_service import (
    DELETE_HAS_CHILDREN,
    DELETE_NOT_FOUND,
    DELETE_OK,
    FolderService,
)
from app.services.media_service import MediaService


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


async def test_create_and_subfolder(sqlite_maker):
    async with sqlite_maker() as s:
        svc = FolderService(s)
        root = await svc.create("Movies")
        sub = await svc.create("2024", parent_id=root.id)
        assert sub.parent_id == root.id
        children = await svc.list_children(root.id)
        assert [c.id for c in children] == [sub.id]
        assert [c.id for c in await svc.list_children(None)] == [root.id]


async def test_create_bad_parent_returns_none(sqlite_maker):
    async with sqlite_maker() as s:
        assert await FolderService(s).create("x", parent_id=999) is None


async def test_rename(sqlite_maker):
    async with sqlite_maker() as s:
        svc = FolderService(s)
        f = await svc.create("old")
        assert await svc.rename(f.id, "new") is True
        assert (await svc.get(f.id)).name == "new"
        assert await svc.rename(999, "x") is False


async def test_delete_blocked_by_children(sqlite_maker):
    async with sqlite_maker() as s:
        svc = FolderService(s)
        root = await svc.create("root")
        await svc.create("child", parent_id=root.id)
        assert await svc.delete(root.id) == DELETE_HAS_CHILDREN
        assert await svc.get(root.id) is not None  # not deleted
        assert await svc.delete(999) == DELETE_NOT_FOUND


async def test_delete_empty_ok(sqlite_maker):
    async with sqlite_maker() as s:
        svc = FolderService(s)
        f = await svc.create("empty")
        assert await svc.delete(f.id) == DELETE_OK
        assert await svc.get(f.id) is None


async def test_set_folder_validates_and_moves(sqlite_maker):
    async with sqlite_maker() as s:
        user = User(telegram_id=1)
        s.add(user)
        await s.commit()
        msvc = MediaService(s)
        media = await msvc.create_media(
            files=[{"telegram_file_id": "f", "file_type": "document"}],
            owner_user_id=user.id,
        )
        folder = await FolderService(s).create("dst")

        # non-existent folder -> no-op False (no FK violation)
        assert await msvc.set_folder(media.id, user.id, 999) is False
        # real folder -> moved
        assert await msvc.set_folder(media.id, user.id, folder.id) is True
        await s.refresh(media)  # _owned_update is a bulk UPDATE; reload the row
        assert media.folder_id == folder.id
        # wrong owner -> no-op
        assert await msvc.set_folder(media.id, user.id + 5, None) is False
        # back to uncategorised
        assert await msvc.set_folder(media.id, user.id, None) is True
        await s.refresh(media)
        assert media.folder_id is None
