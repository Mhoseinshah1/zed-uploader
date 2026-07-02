"""StatsService — bounded aggregate reports for the panel (C3).

Every query is bounded: date ranges are clamped (default: last 30 days, max
span 366 days) and every list carries a LIMIT, so a report can never scan the
whole history unbounded. Buckets use Postgres date_trunc (the app's only
supported production DB).

Also provides the CSV serializer used by the export endpoints.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.download_log import DownloadLog
from app.models.media import Media
from app.models.payment import Payment
from app.models.user import User
from app.models.wallet import WalletTransaction

DEFAULT_DAYS = 30
MAX_SPAN_DAYS = 366
TOP_LIMIT = 10
PERIODS = ("day", "week", "month")


@dataclass
class DateRange:
    start: datetime
    end: datetime


def clamp_range(start: date | None, end: date | None) -> DateRange:
    """Default = last 30 days; span capped at 366 days; end never before start."""
    today = datetime.now(timezone.utc).date()
    end_d = end or today
    start_d = start or (end_d - timedelta(days=DEFAULT_DAYS))
    if start_d > end_d:
        start_d = end_d
    if (end_d - start_d).days > MAX_SPAN_DAYS:
        start_d = end_d - timedelta(days=MAX_SPAN_DAYS)
    return DateRange(
        start=datetime.combine(start_d, time.min, tzinfo=timezone.utc),
        end=datetime.combine(end_d, time.max, tzinfo=timezone.utc),
    )


def rows_to_csv(header: list[str], rows: list[tuple]) -> str:
    """Serialize a report to CSV text (excel-friendly, \r\n line endings)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


class StatsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def downloads_per_period(
        self, rng: DateRange, period: str = "day"
    ) -> list[tuple[str, int]]:
        """[(bucket ISO date, downloads)] for day|week|month buckets."""
        if period not in PERIODS:
            period = "day"
        bucket = func.date_trunc(period, DownloadLog.created_at)
        rows = (
            await self.session.execute(
                select(bucket.label("bucket"), func.count(DownloadLog.id))
                .where(
                    DownloadLog.created_at >= rng.start,
                    DownloadLog.created_at <= rng.end,
                )
                .group_by(bucket)
                .order_by(bucket)
                .limit(MAX_SPAN_DAYS + 1)
            )
        ).all()
        return [(b.date().isoformat(), int(n)) for b, n in rows]

    async def new_users_per_day(self, rng: DateRange) -> list[tuple[str, int]]:
        bucket = func.date_trunc("day", User.created_at)
        rows = (
            await self.session.execute(
                select(bucket.label("bucket"), func.count(User.id))
                .where(User.created_at >= rng.start, User.created_at <= rng.end)
                .group_by(bucket)
                .order_by(bucket)
                .limit(MAX_SPAN_DAYS + 1)
            )
        ).all()
        return [(b.date().isoformat(), int(n)) for b, n in rows]

    async def top_plans_by_sales(
        self, rng: DateRange, limit: int = TOP_LIMIT
    ) -> list[tuple[str, int, int]]:
        """[(plan_key, sales_count, revenue_toman)] from the purchase ledger."""
        rows = (
            await self.session.execute(
                select(
                    WalletTransaction.reference,
                    func.count(WalletTransaction.id),
                    func.coalesce(func.sum(-WalletTransaction.amount), 0),
                )
                .where(
                    WalletTransaction.type == "purchase",
                    WalletTransaction.reference.like("plan:%"),
                    WalletTransaction.created_at >= rng.start,
                    WalletTransaction.created_at <= rng.end,
                )
                .group_by(WalletTransaction.reference)
                .order_by(func.count(WalletTransaction.id).desc())
                .limit(min(limit, TOP_LIMIT))
            )
        ).all()
        return [(ref.split(":", 1)[1], int(n), int(rev)) for ref, n, rev in rows]

    async def top_files_by_downloads(
        self, rng: DateRange, limit: int = TOP_LIMIT
    ) -> list[tuple[str, str, int]]:
        """[(code, title, downloads-in-range)]."""
        count = func.count(DownloadLog.id)
        rows = (
            await self.session.execute(
                select(Media.code, Media.title, count)
                .join(DownloadLog, DownloadLog.media_id == Media.id)
                .where(
                    DownloadLog.created_at >= rng.start,
                    DownloadLog.created_at <= rng.end,
                )
                .group_by(Media.id)
                .order_by(count.desc())
                .limit(min(limit, TOP_LIMIT))
            )
        ).all()
        return [(code, title or "", int(n)) for code, title, n in rows]

    async def revenue_by_method(self, rng: DateRange) -> list[tuple[str, int, int]]:
        """[(method, approved_count, revenue)] — covers card/centralpay/
        zarinpal/zibal and any future method (e.g. telegram_stars) generically."""
        rows = (
            await self.session.execute(
                select(
                    Payment.method,
                    func.count(Payment.id),
                    func.coalesce(func.sum(Payment.amount), 0),
                )
                .where(
                    Payment.status == "approved",
                    Payment.created_at >= rng.start,
                    Payment.created_at <= rng.end,
                )
                .group_by(Payment.method)
                .order_by(func.sum(Payment.amount).desc())
                .limit(20)
            )
        ).all()
        return [(m, int(n), int(rev)) for m, n, rev in rows]

    async def media_counts_by_status(self) -> list[tuple[str, int]]:
        rows = (
            await self.session.execute(
                select(Media.status, func.count(Media.id))
                .group_by(Media.status)
                .order_by(Media.status)
                .limit(10)
            )
        ).all()
        return [(status, int(n)) for status, n in rows]
