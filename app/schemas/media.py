"""Media response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MediaFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_type: str
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    sort_order: int


class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    title: str | None = None
    caption: str | None = None
    download_limit: int | None = None
    download_count: int
    is_active: bool
    protect_content: bool
    auto_delete_seconds: int | None = None
    created_at: datetime
    files: list[MediaFileOut] = []
