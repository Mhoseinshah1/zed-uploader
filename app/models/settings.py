"""Key/value bot settings and feature flags (kept minimal for Phase 1).

FeatureFlag keeps the door open for Phase 2 plan gating (Plus/Max/Pro) without
implementing any Phase 2 behaviour now.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped


class BotSetting(TenantScoped, Base):
    __tablename__ = "bot_settings"
    # settings (and editable texts under "text:*") are per-tenant.
    __table_args__ = (
        Index("uq_bot_settings_tenant_key", "tenant_id", "key", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class FeatureFlag(TenantScoped, Base):
    __tablename__ = "feature_flags"
    __table_args__ = (
        Index("uq_feature_flags_tenant_key", "tenant_id", "key", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    plan: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
