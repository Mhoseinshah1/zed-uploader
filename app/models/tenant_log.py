"""TenantLogSettings — a tenant's Telegram log group + its forum topic ids (G1).

Per-tenant (TenantScoped): one row per tenant, holding the connected forum
supergroup id and the ``message_thread_id`` of each auto-created topic. The
TenantLogger streams redacted operational events to these topics; an unset
group makes logging a silent no-op.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped


class TenantLogSettings(TenantScoped, Base):
    __tablename__ = "tenant_log_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_log_settings_tenant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    log_group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    topic_payments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topic_uploads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topic_errors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topic_new_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topic_backups: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
        return f"<TenantLogSettings tenant={self.tenant_id} group={self.log_group_id}>"
