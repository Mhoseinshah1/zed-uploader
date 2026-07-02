"""Broadcast models — a job plus a per-recipient ledger.

The ``broadcast_recipients`` rows are the source of truth that makes sending
exactly-once and resumable: each row moves ``pending -> sent|failed|blocked``
once, and ``UNIQUE(broadcast_id, user_id)`` guarantees one attempt slot per
(job, user). A worker restart re-reads the ``pending`` rows and never re-sends a
row that already left ``pending``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped


class BroadcastJob(TenantScoped, Base):
    __tablename__ = "broadcast_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # copy_message jobs carry (from_chat_id, message_id); panel text jobs carry text
    from_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default=sql_text("'pending'"),
        index=True,
        nullable=False,
    )  # pending | running | done | failed
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    sent: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    failed: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    blocked: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<BroadcastJob id={self.id} status={self.status!r}>"


class BroadcastRecipient(TenantScoped, Base):
    __tablename__ = "broadcast_recipients"
    __table_args__ = (
        UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_recipient"),
        Index("ix_broadcast_recipients_job_status", "broadcast_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("broadcast_jobs.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default=sql_text("'pending'"),
        nullable=False,
    )  # pending | sent | failed | blocked
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<BroadcastRecipient job={self.broadcast_id} user={self.user_id} {self.status!r}>"
