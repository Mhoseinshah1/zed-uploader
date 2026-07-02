"""Admin model — seeded from ADMIN_IDS on startup."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped


class Admin(TenantScoped, Base):
    __tablename__ = "admins"
    # an admin is an admin of ONE tenant's bot — unique per tenant.
    __table_args__ = (
        Index("uq_admins_tenant_telegram", "tenant_id", "telegram_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), default="owner", server_default=text("'owner'"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Admin id={self.id} telegram_id={self.telegram_id} role={self.role}>"
