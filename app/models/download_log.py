"""DownloadLog model — one row per successful media delivery."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped


class DownloadLog(TenantScoped, Base):
    __tablename__ = "download_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(
        ForeignKey("media.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    # indexed for the stats date-range aggregates (C3) — downloads is the
    # largest table and downloads-per-day scans it by created_at
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<DownloadLog id={self.id} media_id={self.media_id}>"
