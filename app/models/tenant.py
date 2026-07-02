"""Tenant model — one hosted customer bot (Phase F1 multi-tenant foundation).

This is a GLOBAL table (it IS the tenant registry, so it has no ``tenant_id``).
The platform's own bot is tenant id=1 — the first row, seeded by migration 0019
in production and by an ``after_create`` hook when the schema is built via
``create_all`` (tests). ``bot_token`` is encrypted at rest (Fernet, see
``app/core/crypto.py``) and never logged. ``owner_user_id`` is the paying
platform user's id and is intentionally NOT a FK (a FK would create a cycle
tenants -> users -> tenants).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, event, func, insert, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bot_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, index=True, nullable=True
    )
    bot_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Fernet ciphertext of the BotFather token (never stored/logged in plaintext).
    bot_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default=text("'active'"),
        index=True, nullable=False,
    )  # active | suspended | pending | deleted
    plan: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # null = perpetual purchase; set = rental that a worker suspends on expiry.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
        return f"<Tenant id={self.id} bot={self.bot_username!r} status={self.status!r}>"


@event.listens_for(Tenant.__table__, "after_create")
def _seed_platform_tenant(target, connection, **kw) -> None:
    """Seed the platform tenant as the first row (id=1) when the schema is built
    via ``create_all`` (tests). Production seeds it inside migration 0019, which
    is DDL-level and does not fire this ORM event."""
    connection.execute(insert(target).values(bot_username="platform", status="active"))
