"""Advanced stats page + CSV export (owner panel session, rate-limited)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import rate_limit
from app.db.session import get_session
from app.panel.deps import audit, render, require_panel_user
from app.services.stats_service import (
    PERIODS,
    StatsService,
    clamp_range,
    rows_to_csv,
)

router = APIRouter()

REPORTS = ("downloads", "users", "plans", "files", "revenue", "media_status")

_CSV_HEADERS = {
    "downloads": ["date", "downloads"],
    "users": ["date", "new_users"],
    "plans": ["plan", "sales", "revenue"],
    "files": ["code", "title", "downloads"],
    "revenue": ["method", "payments", "revenue"],
    "media_status": ["status", "count"],
}


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


async def _report_rows(
    session: AsyncSession, report: str, rng, period: str
) -> list[tuple]:
    svc = StatsService(session)
    if report == "downloads":
        return await svc.downloads_per_period(rng, period)
    if report == "users":
        return await svc.new_users_per_day(rng)
    if report == "plans":
        return await svc.top_plans_by_sales(rng)
    if report == "files":
        return await svc.top_files_by_downloads(rng)
    if report == "revenue":
        return await svc.revenue_by_method(rng)
    return await svc.media_counts_by_status()


@router.get("/stats")
async def stats_page(
    request: Request,
    start: str = "",
    end: str = "",
    period: str = "day",
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    period = period if period in PERIODS else "day"
    rng = clamp_range(_parse_date(start), _parse_date(end))
    svc = StatsService(session)
    downloads = await svc.downloads_per_period(rng, period)
    users = await svc.new_users_per_day(rng)
    revenue = await svc.revenue_by_method(rng)
    ctx = {
        "rng_start": rng.start.date().isoformat(),
        "rng_end": rng.end.date().isoformat(),
        "period": period,
        "downloads": downloads,
        "downloads_max": max((n for _, n in downloads), default=0),
        "users": users,
        "users_max": max((n for _, n in users), default=0),
        "plans": await svc.top_plans_by_sales(rng),
        "files": await svc.top_files_by_downloads(rng),
        "revenue": revenue,
        "revenue_max": max((rev for _, _, rev in revenue), default=0),
        "media_status": await svc.media_counts_by_status(),
    }
    return render(request, "stats.html", **ctx)


@router.get("/stats/export/{report}.csv", dependencies=[Depends(rate_limit)])
async def stats_export(
    request: Request,
    report: str,
    start: str = "",
    end: str = "",
    period: str = "day",
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    if report not in REPORTS:
        return Response("unknown report", status_code=404)
    period = period if period in PERIODS else "day"
    rng = clamp_range(_parse_date(start), _parse_date(end))
    rows = await _report_rows(session, report, rng, period)
    await audit(session, request, "stats_export", target=report)
    body = rows_to_csv(_CSV_HEADERS[report], rows)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{report}.csv"'},
    )
