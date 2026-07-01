"""Stats response schema."""
from __future__ import annotations

from pydantic import BaseModel


class StatsOut(BaseModel):
    total_users: int
    total_media: int
    total_downloads: int
