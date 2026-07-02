"""WalletTransaction model — the append-only wallet ledger.

Invariant: for any user, SUM(wallet_transactions.amount) == users.balance.
Only WalletService writes these rows (and users.balance) together.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped


class WalletTransaction(TenantScoped, Base):
    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # signed
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
