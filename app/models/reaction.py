"""MediaReaction (J1) — like / dislike / favorite, one per (user, media, kind).

Tenant-scoped. The UNIQUE constraint is the toggle's idempotency anchor; the
denormalized counters live on ``media`` (like_count/dislike_count/
favorite_count) and are updated atomically in the SAME transaction as the
reaction row, so a crash can never skew them.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped

REACTION_KINDS = ("like", "dislike", "favorite")


class MediaReaction(TenantScoped, Base):
    __tablename__ = "media_reactions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "media_id", "user_id", "kind", name="uq_reaction_once"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(
        ForeignKey("media.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # like|dislike|favorite
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<MediaReaction media={self.media_id} user={self.user_id} {self.kind!r}>"
