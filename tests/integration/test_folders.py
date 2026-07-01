"""B2 integration (REAL Postgres): the FK ON DELETE SET NULL behaviour + moves.

The critical guarantee: deleting a folder must NOT delete its media — their
folder_id becomes NULL (only Postgres enforces the FK action, so this needs a
real DB).
"""
from __future__ import annotations

from app.models import Folder, Media, User
from app.services.folder_service import DELETE_HAS_CHILDREN, DELETE_OK, FolderService
from app.services.media_service import MediaService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


async def _user(maker, telegram_id: int) -> int:
    async with maker() as s:
        u = User(telegram_id=telegram_id)
        s.add(u)
        await s.commit()
        return u.id


async def _media_in(maker, owner: int, folder_id: int | None) -> int:
    async with maker() as s:
        m = await MediaService(s).create_media(
            files=[{"telegram_file_id": "f", "file_type": "document"}],
            owner_user_id=owner,
        )
        if folder_id is not None:
            await MediaService(s).set_folder(m.id, owner, folder_id)
        return m.id


# deleting a folder nulls its media's folder_id; the media survive -----------
async def test_delete_folder_nulls_media(pg_sessionmaker):
    uid = await _user(pg_sessionmaker, 7101)
    async with pg_sessionmaker() as s:
        folder = await FolderService(s).create("F")
        fid = folder.id
    mid = await _media_in(pg_sessionmaker, uid, fid)

    async with pg_sessionmaker() as s:
        assert await FolderService(s).delete(fid) == DELETE_OK

    async with pg_sessionmaker() as s:
        media = await s.get(Media, mid)  # survives
        assert media is not None and media.folder_id is None
        assert await s.get(Folder, fid) is None


# deleting a parent with subfolders is blocked ------------------------------
async def test_delete_blocked_with_subfolder(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        svc = FolderService(s)
        root = await svc.create("root")
        await svc.create("sub", parent_id=root.id)
        rid = root.id
    async with pg_sessionmaker() as s:
        assert await FolderService(s).delete(rid) == DELETE_HAS_CHILDREN
        assert await FolderService(s).get(rid) is not None


# move media between folders + owner-scoped folder listing -------------------
async def test_move_and_list_by_folder(pg_sessionmaker):
    uid = await _user(pg_sessionmaker, 7102)
    async with pg_sessionmaker() as s:
        svc = FolderService(s)
        a = await svc.create("A")
        b = await svc.create("B")
        aid, bid = a.id, b.id
    mid = await _media_in(pg_sessionmaker, uid, aid)

    async with pg_sessionmaker() as s:
        msvc = MediaService(s)
        assert await msvc.count_by_folder(aid, uid) == 1
        assert await msvc.count_by_folder(bid, uid) == 0
        # move A -> B
        assert await msvc.set_folder(mid, uid, bid) is True
    async with pg_sessionmaker() as s:
        msvc = MediaService(s)
        assert await msvc.count_by_folder(aid, uid) == 0
        assert [m.id for m in await msvc.list_by_folder(bid, uid)] == [mid]


# subfolder parent_id works + list_children ---------------------------------
async def test_subfolder_listing(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        svc = FolderService(s)
        root = await svc.create("root")
        sub1 = await svc.create("s1", parent_id=root.id)
        sub2 = await svc.create("s2", parent_id=root.id)
        rid = root.id
    async with pg_sessionmaker() as s:
        svc = FolderService(s)
        children = await svc.list_children(rid)
        assert {c.id for c in children} == {sub1.id, sub2.id}
        assert all(c.parent_id == rid for c in children)
        # both roots-only listing excludes the subfolders
        roots = await svc.list_children(None)
        assert rid in {r.id for r in roots}
        assert sub1.id not in {r.id for r in roots}
