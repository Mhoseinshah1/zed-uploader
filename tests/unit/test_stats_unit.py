"""C3 unit tests — range clamping, CSV shape, export auth, rate limit."""
from __future__ import annotations

import csv
import io
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.stats_service import MAX_SPAN_DAYS, clamp_range, rows_to_csv


def test_clamp_range_defaults_and_cap():
    rng = clamp_range(None, None)
    assert (rng.end.date() - rng.start.date()).days == 30  # default window

    # oversized span gets capped
    rng = clamp_range(date(2020, 1, 1), date(2024, 1, 1))
    assert (rng.end.date() - rng.start.date()).days == MAX_SPAN_DAYS

    # inverted range collapses instead of erroring
    rng = clamp_range(date(2024, 5, 10), date(2024, 5, 1))
    assert rng.start.date() == rng.end.date() == date(2024, 5, 1)


def test_rows_to_csv_well_formed():
    body = rows_to_csv(
        ["plan", "sales", "revenue"],
        [("plus", 3, 30000), ('has,"comma"', 1, 5)],
    )
    parsed = list(csv.reader(io.StringIO(body)))
    assert parsed[0] == ["plan", "sales", "revenue"]
    assert parsed[1] == ["plus", "3", "30000"]
    assert parsed[2] == ['has,"comma"', "1", "5"]  # quoting survives round-trip


async def test_export_requires_panel_session():
    import httpx
    from httpx import ASGITransport

    from app.api.main import app

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/panel/stats/export/downloads.csv", follow_redirects=False
        )
    assert resp.status_code == 302  # bounced to the panel login
    assert "/panel/login" in resp.headers["location"]


async def test_rate_limit_trips_after_threshold():
    from app.api.deps import RATE_LIMIT, rate_limit

    request = SimpleNamespace(client=SimpleNamespace(host="9.9.9.9"))
    for _ in range(RATE_LIMIT):
        await rate_limit(request)  # under the limit: silent
    with pytest.raises(HTTPException) as exc:
        await rate_limit(request)
    assert exc.value.status_code == 429
