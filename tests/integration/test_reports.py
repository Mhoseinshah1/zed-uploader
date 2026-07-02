"""D2 integration (REAL Postgres): deactivation hides the media everywhere."""
from __future__ import annotations

from unittest.mock import AsyncMock

import app.bot.delivery as delivery
from app.bot.delivery import DeliveryStatus, deliver_by_code
from app.models import Media, MediaFile, User
from app.services.media_service import MediaService
from app.services.report_service import ReportService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


async def test_deactivation_hides_from_delivery_and_search(pg_sessionmaker, monkeypatch):
    monkeypatch.setattr(delivery, "send_media_file", AsyncMock(return_value=1))
    async with pg_sessionmaker() as s:
        user = User(telegram_id=6601)
        media = Media(code="repX", status="approved", title="searchable target")
        media.files.append(
            MediaFile(sort_order=0, telegram_file_id="f", file_type="document")
        )
        s.add_all([user, media])
        await s.commit()
        uid, mid, code = user.id, media.id, media.code

    # delivered + publicly searchable before the report is accepted
    async with pg_sessionmaker() as s:
        assert (await deliver_by_code(AsyncMock(), s, 5, None, code)).status \
            is DeliveryStatus.DELIVERED
        _, total = await MediaService(s).search("searchable", approved_only=True)
        assert total == 1

    async with pg_sessionmaker() as s:
        svc = ReportService(s)
        await svc.create(mid, uid, "inappropriate")
        report = (await svc.list_reports(status="pending"))[0]
        assert await svc.review_deactivate(report.id, admin_id=42) is True

    async with pg_sessionmaker() as s:
        result = await deliver_by_code(AsyncMock(), s, 5, None, code)
        assert result.status is DeliveryStatus.INACTIVE  # no longer delivered
        _, total = await MediaService(s).search("searchable", approved_only=True)
        assert total == 0  # hidden from public search too
