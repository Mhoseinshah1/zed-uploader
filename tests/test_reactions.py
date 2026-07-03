"""J1 — reactions: toggles, counters, favorites, sorted views, tenant scope."""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Base, Media, MediaReaction, Tenant, User
from app.services.reaction_service import ReactionService

T_A, T_B = 2, 3


@pytest_asyncio.fixture
async def sm():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    with all_tenants():
        async with Session() as s:
            s.add_all([
                Tenant(id=T_A, bot_username="a", bot_id=2002, status="active"),
                Tenant(id=T_B, bot_username="b", bot_id=3003, status="active"),
            ])
            await s.commit()
    try:
        yield Session
    finally:
        await engine.dispose()


async def _seed(sm, tenant=T_A):
    with tenant_scope(tenant):
        async with sm() as s:
            u = User(telegram_id=90000 + tenant)
            m = Media(code=f"R{tenant}", status="approved")
            s.add_all([u, m])
            await s.commit()
            return u.id, m.id


async def test_toggle_like_unlike_and_counter(sm):
    uid, mid = await _seed(sm)
    with tenant_scope(T_A):
        async with sm() as s:
            svc = ReactionService(s)
            assert await svc.toggle(mid, uid, "like") is True   # set
        async with sm() as s:
            m = await s.get(Media, mid)
            assert m.like_count == 1
            assert await ReactionService(s).toggle(mid, uid, "like") is False  # unset
        async with sm() as s:
            m = await s.get(Media, mid)
            assert m.like_count == 0
            n = int(await s.scalar(select(func.count(MediaReaction.id))))
            assert n == 0  # row removed


async def test_like_dislike_mutually_exclusive_one_per_kind(sm):
    uid, mid = await _seed(sm)
    with tenant_scope(T_A):
        async with sm() as s:
            svc = ReactionService(s)
            await svc.toggle(mid, uid, "like")
            await svc.toggle(mid, uid, "dislike")  # clears the like
        async with sm() as s:
            m = await s.get(Media, mid)
            assert (m.like_count, m.dislike_count) == (0, 1)
            kinds = set(
                (await s.scalars(select(MediaReaction.kind))).all()
            )
            assert kinds == {"dislike"}  # never both, never duplicates
        # favorite is independent of like/dislike
        async with sm() as s:
            await ReactionService(s).toggle(mid, uid, "favorite")
        async with sm() as s:
            m = await s.get(Media, mid)
            assert m.favorite_count == 1 and m.dislike_count == 1


async def test_favorites_list_only_approved_active(sm):
    uid, mid = await _seed(sm)
    with tenant_scope(T_A):
        async with sm() as s:
            hidden = Media(code="HIDDEN", status="pending")
            inactive = Media(code="OFF", status="approved", is_active=False)
            s.add_all([hidden, inactive])
            await s.commit()
            svc = ReactionService(s)
            await svc.toggle(mid, uid, "favorite")
            await svc.toggle(hidden.id, uid, "favorite")
            await svc.toggle(inactive.id, uid, "favorite")
        async with sm() as s:
            favs = await ReactionService(s).favorites(uid)
            assert [m.code for m in favs] == ["R2"]  # hidden/inactive excluded


async def test_sorted_views_scope_and_order(sm):
    uid, mid = await _seed(sm)
    with tenant_scope(T_A):
        async with sm() as s:
            hot = Media(code="HOT", status="approved", download_count=99)
            s.add(hot)
            pending = Media(code="PEND", status="pending", download_count=1000)
            s.add(pending)
            await s.commit()
            await ReactionService(s).toggle(hot.id, uid, "like")
        async with sm() as s:
            svc = ReactionService(s)
            popular = [m.code for m in await svc.listing("popular")]
            most_viewed = [m.code for m in await svc.listing("most_viewed")]
            newest = [m.code for m in await svc.listing("newest")]
    assert popular[0] == "HOT"                # sorted by likes
    assert most_viewed[0] == "HOT"            # sorted by downloads
    assert newest[0] == "HOT"                 # newest id first
    for lst in (popular, most_viewed, newest):
        assert "PEND" not in lst              # pending never shown

    # tenant B sees NOTHING of tenant A
    ub, mb = await _seed(sm, tenant=T_B)
    with tenant_scope(T_B):
        async with sm() as s:
            codes = [m.code for m in await ReactionService(s).listing("popular")]
            assert codes == ["R3"]  # only its own
            favs = await ReactionService(s).favorites(uid)  # A's user id
            assert favs == []  # cross-tenant favorites invisible


async def test_reaction_isolated_across_tenants(sm):
    uid_a, mid_a = await _seed(sm, tenant=T_A)
    with tenant_scope(T_B):
        async with sm() as s:
            # tenant B context cannot even see tenant A's media -> toggle no-ops
            assert await ReactionService(s).toggle(mid_a, uid_a, "like") is False
    with tenant_scope(T_A):
        async with sm() as s:
            m = await s.get(Media, mid_a)
            assert m.like_count == 0  # untouched
