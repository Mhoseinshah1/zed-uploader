"""ReportService — file, list, and review media abuse reports (D2)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.media import Media
from app.models.media_report import REPORT_REASONS, MediaReport

log = get_logger("reports")

CREATED = "created"
DUPLICATE = "duplicate"


class ReportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, media_id: int, user_id: int, reason: str, description: str | None = None
    ) -> str:
        """File a report; a repeat by the same user on the same media is deduped."""
        if reason not in REPORT_REASONS:
            reason = "other"
        existing = await self.session.scalar(
            select(MediaReport.id).where(
                MediaReport.media_id == media_id, MediaReport.user_id == user_id
            )
        )
        if existing is not None:
            return DUPLICATE
        self.session.add(
            MediaReport(
                media_id=media_id, user_id=user_id, reason=reason,
                description=description,
            )
        )
        try:
            await self.session.commit()
        except IntegrityError:  # concurrent double-tap on the unique constraint
            await self.session.rollback()
            return DUPLICATE
        log.info("report_created", media_id=media_id, user_id=user_id, reason=reason)
        return CREATED

    async def get(self, report_id: int) -> MediaReport | None:
        return await self.session.get(MediaReport, report_id)

    async def list_reports(
        self, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[MediaReport]:
        stmt = select(MediaReport)
        if status:
            stmt = stmt.where(MediaReport.status == status)
        result = await self.session.scalars(
            stmt.order_by(MediaReport.id.desc()).limit(limit).offset(offset)
        )
        return list(result.all())

    async def count_pending(self) -> int:
        return int(
            await self.session.scalar(
                select(func.count(MediaReport.id)).where(
                    MediaReport.status == "pending"
                )
            )
            or 0
        )

    async def review_deactivate(
        self, report_id: int, admin_id: int | None
    ) -> bool:
        """Accept the report: deactivate the media (hidden from delivery+search)
        and mark every pending report on it reviewed."""
        report = await self.get(report_id)
        if report is None or report.status != "pending":
            return False
        media = await self.session.get(Media, report.media_id)
        if media is not None:
            media.is_active = False
        now = datetime.now(timezone.utc)
        siblings = await self.session.scalars(
            select(MediaReport).where(
                MediaReport.media_id == report.media_id,
                MediaReport.status == "pending",
            )
        )
        for row in siblings:
            row.status = "reviewed"
            row.reviewed_by_admin_id = admin_id
            row.reviewed_at = now
        await self.session.commit()
        log.info("report_accepted", report_id=report_id, media_id=report.media_id)
        return True

    async def dismiss(self, report_id: int, admin_id: int | None) -> bool:
        report = await self.get(report_id)
        if report is None or report.status != "pending":
            return False
        report.status = "dismissed"
        report.reviewed_by_admin_id = admin_id
        report.reviewed_at = datetime.now(timezone.utc)
        await self.session.commit()
        log.info("report_dismissed", report_id=report_id)
        return True
