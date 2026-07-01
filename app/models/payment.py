"""Payment model — a top-up request awaiting owner approval."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)  # card|zarinpal|crypto
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default=text("'pending'"),
        index=True,
        nullable=False,
    )
    receipt: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # --- CentralPay (Phase 5) --------------------------------------------
    provider_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)  # topup | plan:<key>
    # --- gateway seam (Phase C1) ------------------------------------------
    # provider key for online payments ("centralpay"|"zarinpal"); legacy rows
    # predate the column (NULL) and fall back to `method`, which carries the
    # same key for gateway payments.
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # gateway-issued transaction token (Zarinpal Authority) so the GET return
    # can be resolved back to our order.
    authority: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
