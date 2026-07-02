"""D2 unit tests — report create/dedup, review actions, panel auth."""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base, Media, MediaFile, User
from app.services.report_service import CREATED, DUPLICATE, ReportService


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


async def _seed(s):
    user = User(telegram_id=61)
    media = Media(code="rep1", status="approved")
    media.files.append(MediaFile(sort_order=0, telegram_file_id="f", file_type="document"))
    s.add_all([user, media])
    await s.commit()
    return user, media


async def test_create_and_dedup(sqlite_maker):
    async with sqlite_maker() as s:
        user, media = await _seed(s)
        svc = ReportService(s)
        assert await svc.create(media.id, user.id, "spam") == CREATED
        # the same user reporting the same media again is deduped
        assert await svc.create(media.id, user.id, "copyright") == DUPLICATE
        assert len(await svc.list_reports(status="pending")) == 1
        # a different user can still report it
        other = User(telegram_id=62)
        s.add(other)
        await s.commit()
        assert await svc.create(media.id, other.id, "other") == CREATED
        assert await svc.count_pending() == 2


async def test_unknown_reason_folds_to_other(sqlite_maker):
    async with sqlite_maker() as s:
        user, media = await _seed(s)
        svc = ReportService(s)
        await svc.create(media.id, user.id, "hacked-value")
        report = (await svc.list_reports())[0]
        assert report.reason == "other"


async def test_review_deactivates_media_and_sibling_reports(sqlite_maker):
    async with sqlite_maker() as s:
        user, media = await _seed(s)
        other = User(telegram_id=63)
        s.add(other)
        await s.commit()
        svc = ReportService(s)
        await svc.create(media.id, user.id, "spam")
        await svc.create(media.id, other.id, "copyright")
        first = (await svc.list_reports(status="pending"))[-1]

        assert await svc.review_deactivate(first.id, admin_id=999) is True
        await s.refresh(media)
        assert media.is_active is False  # hidden from delivery + search
        # both pending reports on the media are settled together
        assert await svc.count_pending() == 0
        reviewed = await svc.list_reports(status="reviewed")
        assert len(reviewed) == 2
        assert all(r.reviewed_by_admin_id == 999 and r.reviewed_at for r in reviewed)
        # re-reviewing a settled report is a no-op
        assert await svc.review_deactivate(first.id, admin_id=1) is False


async def test_dismiss(sqlite_maker):
    async with sqlite_maker() as s:
        user, media = await _seed(s)
        svc = ReportService(s)
        await svc.create(media.id, user.id, "spam")
        report = (await svc.list_reports())[0]
        assert await svc.dismiss(report.id, admin_id=7) is True
        await s.refresh(media)
        assert media.is_active is True  # dismissing never touches the media
        assert (await svc.list_reports(status="dismissed"))[0].id == report.id
        assert await svc.dismiss(report.id, admin_id=7) is False  # already settled


async def test_panel_reports_require_session():
    import httpx
    from httpx import ASGITransport

    from app.api.main import app

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/panel/reports", follow_redirects=False)
        assert resp.status_code == 302 and "/panel/login" in resp.headers["location"]
        resp = await client.post("/panel/reports/1/deactivate", follow_redirects=False)
        assert resp.status_code == 302 and "/panel/login" in resp.headers["location"]
