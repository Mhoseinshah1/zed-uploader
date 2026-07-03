"""Support tickets (H2) — in-bot ticketing, tenant-scoped.

Two layers, distinguished by ``SupportTicket.target``:
  * ``tenant_admin`` — an end-user of a tenant bot opens a ticket to that
    tenant's admins (answered from the tenant's panel/bot).
  * ``platform`` — a reseller (a tenant admin/owner) opens a ticket to the
    platform operator; these surface ONLY in the super-admin panel inbox.

Both tables inherit ``TenantScoped`` (indexed NOT NULL ``tenant_id`` + the
fail-closed guard), so a ticket and its messages always belong to exactly one
tenant and are never reachable cross-tenant except via the audited super-admin
surface (``ALL_TENANTS``).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped

TICKET_STATUSES = ("open", "answered", "closed")
TICKET_TARGETS = ("tenant_admin", "platform")
SENDER_KINDS = ("user", "admin")


class SupportTicket(TenantScoped, Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    opener_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="open", server_default=sql_text("'open'"),
        index=True, nullable=False,
    )  # open | answered | closed
    target: Mapped[str] = mapped_column(
        String(16), default="tenant_admin",
        server_default=sql_text("'tenant_admin'"), index=True, nullable=False,
    )  # tenant_admin | platform
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<SupportTicket id={self.id} {self.target!r} {self.status!r}>"


class TicketMessage(TenantScoped, Base):
    __tablename__ = "ticket_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    sender_kind: Mapped[str] = mapped_column(String(8), nullable=False)  # user | admin
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<TicketMessage id={self.id} ticket={self.ticket_id} {self.sender_kind!r}>"
