"""MediaPreview (J5) — a preview posted to the tenant's channel, once per media.

Tenant-scoped; UNIQUE(tenant_id, media_id) makes the auto-post idempotent (an
approve retried or reached from several sites never double-posts).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped


class MediaPreview(TenantScoped, Base):
    __tablename__ = "media_previews"
    __table_args__ = (
        UniqueConstraint("tenant_id", "media_id", name="uq_preview_once"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(
        ForeignKey("media.id", ondelete="CASCADE"), index=True, nullable=False
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<MediaPreview media={self.media_id} channel={self.channel_id}>"
