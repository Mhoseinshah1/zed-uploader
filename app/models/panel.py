"""Web-panel models: local login accounts and an audit trail.

These are GLOBAL tables (not tenant-scoped): login must resolve a user by
username BEFORE a tenant context exists, so they carry a plain ``tenant_id``
column instead of the TenantScoped mixin. F4 binds each customer login to its
tenant; ``panel_users.tenant_id`` is NOT NULL (platform admins map to tenant 1).
F5 adds ``is_superadmin`` for the cross-tenant super-admin surface.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PanelUser(Base):
    __tablename__ = "panel_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # the tenant this login manages (F4). platform admins -> tenant 1.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PanelAudit(Base):
    __tablename__ = "panel_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    panel_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # the tenant an action acted on (F4). NULL = a platform/super-admin action.
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), index=True, nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
