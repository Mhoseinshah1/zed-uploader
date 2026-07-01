"""B3 integration (REAL Postgres): MediaService.search — matching, gating,
pagination, and LIKE-wildcard escaping."""
from __future__ import annotations

from app.models import Media, MediaFile, User
from app.services.media_service import MediaService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


async def _user(maker, telegram_id: int) -> int:
    async with maker() as s:
        u = User(telegram_id=telegram_id)
        s.add(u)
        await s.commit()
        return u.id


async def _media(maker, owner, *, title=None, caption=None, status="approved", file_name=None):
    async with maker() as s:
        m = Media(
            code=f"c{title or caption or file_name or 'x'}"[:20],
            owner_user_id=owner, title=title, caption=caption, status=status,
        )
        m.files.append(
            MediaFile(sort_order=0, telegram_file_id="fid", file_type="document", file_name=file_name)
        )
        s.add(m)
        await s.commit()
        return m.id


async def test_matches_title_caption_filename(pg_sessionmaker):
    uid = await _user(pg_sessionmaker, 6001)
    await _media(pg_sessionmaker, uid, title="Holiday Photos")
    await _media(pg_sessionmaker, uid, caption="beach sunset")
    await _media(pg_sessionmaker, uid, file_name="report2024.pdf")

    async with pg_sessionmaker() as s:
        svc = MediaService(s)
        assert (await svc.search("holiday", owner_user_id=uid))[1] == 1
        assert (await svc.search("beach", owner_user_id=uid))[1] == 1
        assert (await svc.search("report2024", owner_user_id=uid))[1] == 1
        assert (await svc.search("nope", owner_user_id=uid))[1] == 0


async def test_matches_by_code(pg_sessionmaker):
    uid = await _user(pg_sessionmaker, 6002)
    async with pg_sessionmaker() as s:
        m = Media(code="ABCXYZ123", owner_user_id=uid, status="approved")
        m.files.append(MediaFile(sort_order=0, telegram_file_id="f", file_type="document"))
        s.add(m)
        await s.commit()
    async with pg_sessionmaker() as s:
        items, total = await MediaService(s).search("ABCXYZ", owner_user_id=uid)
        assert total == 1 and items[0].code == "ABCXYZ123"


async def test_public_search_only_approved_active(pg_sessionmaker):
    owner = await _user(pg_sessionmaker, 6003)
    await _media(pg_sessionmaker, owner, title="Secret doc", status="pending")
    await _media(pg_sessionmaker, owner, title="Secret file", status="approved")
    # approved-but-inactive should also be hidden from public search
    async with pg_sessionmaker() as s:
        m = Media(code="inact1", owner_user_id=owner, title="Secret x", status="approved", is_active=False)
        m.files.append(MediaFile(sort_order=0, telegram_file_id="f", file_type="document"))
        s.add(m)
        await s.commit()

    async with pg_sessionmaker() as s:
        svc = MediaService(s)
        # public: only the approved+active one
        pub_items, pub_total = await svc.search("secret", approved_only=True)
        assert pub_total == 1 and pub_items[0].title == "Secret file"
        # admin (owner): sees all three (pending + approved + inactive)
        _, admin_total = await svc.search("secret", owner_user_id=owner)
        assert admin_total == 3


async def test_pagination_and_limit(pg_sessionmaker):
    uid = await _user(pg_sessionmaker, 6004)
    for i in range(7):
        await _media(pg_sessionmaker, uid, title=f"doc common {i}")
    async with pg_sessionmaker() as s:
        svc = MediaService(s)
        page0, total = await svc.search("common", owner_user_id=uid, limit=5, offset=0)
        page1, _ = await svc.search("common", owner_user_id=uid, limit=5, offset=5)
        assert total == 7 and len(page0) == 5 and len(page1) == 2
        # limit is capped at MAX_SEARCH_LIMIT
        capped, _ = await svc.search("common", owner_user_id=uid, limit=9999)
        assert len(capped) <= MediaService.MAX_SEARCH_LIMIT


async def test_like_wildcards_escaped(pg_sessionmaker):
    uid = await _user(pg_sessionmaker, 6005)
    await _media(pg_sessionmaker, uid, title="100% cotton")
    await _media(pg_sessionmaker, uid, title="plain text")

    async with pg_sessionmaker() as s:
        svc = MediaService(s)
        # a literal '%' must match only the row that contains it, not act as a
        # wildcard that returns everything
        items, total = await svc.search("100%", owner_user_id=uid)
        assert total == 1 and items[0].title == "100% cotton"
        # '_' is a single-char wildcard in LIKE; escaped it matches literally
        assert (await svc.search("_", owner_user_id=uid))[1] == 0
        # empty query returns nothing (no full-table dump)
        assert (await svc.search("   ", owner_user_id=uid))[1] == 0
