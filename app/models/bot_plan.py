"""BotPlan model — master-bot pricing for the buy-a-bot flow (Phase F3).

GLOBAL table (platform-level pricing, not per-customer): the platform sells the
right to create a hosted bot. ``duration_days == 0`` is a perpetual purchase;
``> 0`` is a rental period (sets ``tenants.expires_at``). Configurable from the
platform panel; price is in Toman (charged from the customer's platform wallet).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BotPlan(Base):
    __tablename__ = "bot_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    duration_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )  # 0 = perpetual, >0 = rental
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
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
        return f"<BotPlan {self.key!r} price={self.price} days={self.duration_days}>"
