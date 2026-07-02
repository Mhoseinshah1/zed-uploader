"""Ad model — owner-authored promo shown around file delivery / start.

``placement`` ∈ {before_file, after_file, start_message}. ``target_plan``
restricts the audience to one effective plan (e.g. "free"); NULL = everyone.
``impression_limit`` stops the ad once ``impression_count`` reaches it.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped

PLACEMENTS = ("before_file", "after_file", "start_message")


class Ad(TenantScoped, Base):
    __tablename__ = "ads"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    button_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    button_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    placement: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    target_plan: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("true"), nullable=False
    )
    impression_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    impression_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    click_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Ad id={self.id} placement={self.placement!r} active={self.is_active}>"
