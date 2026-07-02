"""MediaReport model — a user flagging a delivered media as abusive.

One report per (media, user): repeat reports by the same user are deduped via
the unique constraint (policy: dedup, not counting).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped

REPORT_REASONS = ("copyright", "inappropriate", "spam", "other")


class MediaReport(TenantScoped, Base):
    __tablename__ = "media_reports"
    __table_args__ = (
        UniqueConstraint("media_id", "user_id", name="uq_media_report_once"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(
        ForeignKey("media.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default=sql_text("'pending'"),
        index=True, nullable=False,
    )  # pending | reviewed | dismissed
    reviewed_by_admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<MediaReport id={self.id} media={self.media_id} {self.status!r}>"
