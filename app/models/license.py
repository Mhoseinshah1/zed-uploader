"""LicenseInfo model — the single licensing row (mirrored to license.json).

FINGERPRINT (documented contract): ``sha256(machine_id + ":" + install_path)``
hex digest, where ``machine_id`` is the stripped contents of
``/etc/machine-id`` ("" when the file is unreadable) and ``install_path`` is
the absolute path of the project root (the parent of the ``app`` package).
Both inputs are stable across restarts, so the fingerprint is too.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LicenseInfo(Base):
    __tablename__ = "license"

    id: Mapped[int] = mapped_column(primary_key=True)
    license_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="inactive", server_default=sql_text("'inactive'"),
        nullable=False,
    )  # inactive | active | expired | revoked
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    allowed_install_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_ok_at: Mapped[datetime | None] = mapped_column(
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
        return f"<LicenseInfo status={self.status!r} expires={self.expires_at}>"
