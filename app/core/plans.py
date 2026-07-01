"""Shared plan hierarchy."""
from __future__ import annotations

PLAN_ORDER = {"free": 0, "plus": 1, "max": 2}


def plan_rank(plan_key: str | None) -> int:
    return PLAN_ORDER.get(plan_key or "free", 0)
