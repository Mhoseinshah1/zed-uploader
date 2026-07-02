"""C2 unit tests — ad picking rules, counters, and the click-through route.

SQLite; no network.
"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Ad, Base
from app.services.ad_service import AdService


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


async def test_pick_filters_placement_active_and_plan(sqlite_maker):
    async with sqlite_maker() as s:
        svc = AdService(s)
        await svc.create(title="A", text="t", placement="before_file")
        await svc.create(title="B", text="t", placement="after_file")
        await svc.create(title="C", text="t", placement="before_file", target_plan="free")
        off = await svc.create(title="D", text="t", placement="before_file")
        await svc.toggle(off.id)  # deactivate

        before_free = await svc.pick_for_placement("before_file", "free")
        assert [a.title for a in before_free] == ["A", "C"]
        # a plus user must not see the free-only ad; inactive stays hidden
        before_plus = await svc.pick_for_placement("before_file", "plus")
        assert [a.title for a in before_plus] == ["A"]
        assert [a.title for a in await svc.pick_for_placement("after_file", "free")] == ["B"]


async def test_impression_limit_stops_display(sqlite_maker):
    async with sqlite_maker() as s:
        svc = AdService(s)
        ad = await svc.create(
            title="L", text="t", placement="before_file", impression_limit=2
        )
        assert len(await svc.pick_for_placement("before_file", "free")) == 1
        await svc.record_impression(ad.id)
        assert len(await svc.pick_for_placement("before_file", "free")) == 1
        await svc.record_impression(ad.id)
        # limit reached -> no longer shown
        assert await svc.pick_for_placement("before_file", "free") == []
        refreshed = await svc.get(ad.id)
        await s.refresh(refreshed)
        assert refreshed.impression_count == 2


async def test_record_click_increments_and_returns_url(sqlite_maker):
    async with sqlite_maker() as s:
        svc = AdService(s)
        ad = await svc.create(
            title="K", text="t", placement="after_file",
            button_text="go", button_url="https://example.com/x",
        )
        assert await svc.record_click(ad.id) == "https://example.com/x"
        refreshed = await svc.get(ad.id)
        await s.refresh(refreshed)
        assert refreshed.click_count == 1
        # unknown ad / ad without a URL -> None, nothing incremented
        assert await svc.record_click(9999) is None
        no_url = await svc.create(title="N", text="t", placement="after_file")
        assert await svc.record_click(no_url.id) is None


async def test_click_route_redirects_and_counts():
    import httpx
    from httpx import ASGITransport

    from app.api.main import app
    from app.db.session import get_session

    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as s:
        ad = await AdService(s).create(
            title="R", text="t", placement="after_file",
            button_text="go", button_url="https://example.com/promo",
        )
        ad_id = ad.id

    async def _override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/ad/{ad_id}/click", follow_redirects=False)
            assert resp.status_code == 302
            assert resp.headers["location"] == "https://example.com/promo"
            # unknown ad still redirects (to the bot), never 500s
            resp = await client.get("/ad/424242/click", follow_redirects=False)
            assert resp.status_code == 302
            assert "t.me/" in resp.headers["location"]
        async with maker() as s:
            ad = await AdService(s).get(ad_id)
            assert ad.click_count == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
