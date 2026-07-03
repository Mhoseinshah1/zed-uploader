"""MediaPurchase (J6) — a settled per-user entitlement to a paid media.

Written in the SAME transaction as the wallet debit (mirroring the atomic plan
purchase), so an entitlement can never exist without its charge or vice versa.
UNIQUE(tenant_id, media_id, user_id) is the DB-enforced exactly-once anchor.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped


class MediaPurchase(TenantScoped, Base):
    __tablename__ = "media_purchases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "media_id", "user_id", name="uq_media_purchase_once"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(
        ForeignKey("media.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<MediaPurchase media={self.media_id} user={self.user_id} {self.amount}>"
