"""BackupJob model — one pg_dump run (manual or scheduled) with its lifecycle."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped


class BackupJob(TenantScoped, Base):
    __tablename__ = "backup_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(
        String(16), default="manual", server_default=sql_text("'manual'"),
        nullable=False,
    )  # manual | scheduled
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default=sql_text("'pending'"),
        index=True, nullable=False,
    )  # pending | running | success | failed
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<BackupJob id={self.id} {self.type!r} {self.status!r}>"
