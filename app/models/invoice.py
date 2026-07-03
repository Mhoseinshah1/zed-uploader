"""Invoice model (H4) — a receipt row for every settled payment/charge.

A pure RECORD layer on top of the existing settled events (top-ups, plan
purchases, bot creation/rental). Tenant-scoped; ``invoice_no`` is a per-tenant
sequential number; ``source_ref`` is a stable per-settlement key so a retry /
double-callback can never create a duplicate (enforced by uq_invoice_source).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped

INVOICE_KINDS = ("topup", "plan", "bot_creation", "rental")


class Invoice(TenantScoped, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_ref", name="uq_invoice_source"),
        UniqueConstraint("tenant_id", "invoice_no", name="uq_invoice_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # see INVOICE_KINDS
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # stable idempotency key of the settled event (e.g. "payment:12", "sub:9")
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    invoice_no: Mapped[int] = mapped_column(nullable=False)  # sequential per tenant
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Invoice id={self.id} no={self.invoice_no} {self.kind!r} {self.amount}>"
