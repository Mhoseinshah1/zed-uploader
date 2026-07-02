"""C3 integration (REAL Postgres): StatsService aggregates on seeded data."""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.models import (
    DownloadLog,
    Media,
    Payment,
    User,
    WalletTransaction,
)
from app.services.stats_service import StatsService, clamp_range
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


def _dt(day: int, hour: int = 12) -> datetime:
    return datetime(2024, 6, day, hour, tzinfo=timezone.utc)


RNG = clamp_range(date(2024, 6, 1), date(2024, 6, 30))


async def _seed(maker):
    async with maker() as s:
        u1 = User(telegram_id=1101, created_at=_dt(1))
        u2 = User(telegram_id=1102, created_at=_dt(1))
        u3 = User(telegram_id=1103, created_at=_dt(2))
        m1 = Media(code="statA", status="approved", title="فایل الف")
        m2 = Media(code="statB", status="pending")
        s.add_all([u1, u2, u3, m1, m2])
        await s.flush()

        # downloads: 3 on day 1 (2 for m1), 1 on day 2
        s.add_all(
            [
                DownloadLog(media_id=m1.id, telegram_id=1, created_at=_dt(1)),
                DownloadLog(media_id=m1.id, telegram_id=2, created_at=_dt(1)),
                DownloadLog(media_id=m2.id, telegram_id=3, created_at=_dt(1)),
                DownloadLog(media_id=m1.id, telegram_id=4, created_at=_dt(2)),
            ]
        )
        # plan sales ledger: two plus (1000 each), one max (5000)
        s.add_all(
            [
                WalletTransaction(
                    user_id=u1.id, amount=-1000, type="purchase",
                    balance_after=0, reference="plan:plus", created_at=_dt(3),
                ),
                WalletTransaction(
                    user_id=u2.id, amount=-1000, type="purchase",
                    balance_after=0, reference="plan:plus", created_at=_dt(4),
                ),
                WalletTransaction(
                    user_id=u3.id, amount=-5000, type="purchase",
                    balance_after=0, reference="plan:max", created_at=_dt(4),
                ),
            ]
        )
        # payments: approved card 2000 + approved zibal 7000 + pending zibal 999
        s.add_all(
            [
                Payment(user_id=u1.id, amount=2000, method="card",
                        status="approved", created_at=_dt(5)),
                Payment(user_id=u2.id, amount=7000, method="zibal",
                        status="approved", created_at=_dt(5)),
                Payment(user_id=u3.id, amount=999, method="zibal",
                        status="pending", created_at=_dt(5)),
            ]
        )
        await s.commit()


async def test_aggregates_on_seeded_data(pg_sessionmaker):
    await _seed(pg_sessionmaker)
    async with pg_sessionmaker() as s:
        svc = StatsService(s)

        downloads = await svc.downloads_per_period(RNG, "day")
        assert downloads == [("2024-06-01", 3), ("2024-06-02", 1)]

        # month bucketing folds everything into June
        monthly = await svc.downloads_per_period(RNG, "month")
        assert monthly == [("2024-06-01", 4)]

        users = await svc.new_users_per_day(RNG)
        assert users == [("2024-06-01", 2), ("2024-06-02", 1)]

        plans = await svc.top_plans_by_sales(RNG)
        assert plans[0] == ("plus", 2, 2000)  # most sales first
        assert ("max", 1, 5000) in plans

        files = await svc.top_files_by_downloads(RNG)
        assert files[0] == ("statA", "فایل الف", 3)
        assert files[1][0] == "statB" and files[1][2] == 1

        revenue = await svc.revenue_by_method(RNG)
        assert ("zibal", 1, 7000) in revenue  # pending 999 excluded
        assert ("card", 1, 2000) in revenue

        status_counts = dict(await svc.media_counts_by_status())
        assert status_counts == {"approved": 1, "pending": 1}


async def test_range_bounds_respected(pg_sessionmaker):
    await _seed(pg_sessionmaker)
    async with pg_sessionmaker() as s:
        svc = StatsService(s)
        # a range that excludes day 2 only counts day 1
        rng = clamp_range(date(2024, 6, 1), date(2024, 6, 1))
        assert await svc.downloads_per_period(rng, "day") == [("2024-06-01", 3)]
        # an empty range returns nothing (no unbounded scan)
        empty = clamp_range(date(2023, 1, 1), date(2023, 1, 2))
        assert await svc.downloads_per_period(empty, "day") == []
