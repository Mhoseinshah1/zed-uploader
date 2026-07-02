"""PaymentProviderConfig — per-gateway runtime configuration.

One row per provider key ("centralpay", "zarinpal", ...). Editable from the
panel so owners can enable/disable a gateway or set a merchant id without a
redeploy. Secret API keys (CentralPay) stay in the environment; this table only
holds non-secret merchant ids and flags.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaymentProviderConfig(Base):
    __tablename__ = "payment_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sandbox: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    extra: Mapped[str | None] = mapped_column(Text, nullable=True)
    # per-provider credentials/params (C1b): zarinpal {merchant_id}, zibal
    # {merchant}, centralpay {getlink_key, verify_key}. Secrets live here (or
    # in env for CentralPay's fallback) and are masked in the panel.
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
        return f"<PaymentProviderConfig {self.key!r} enabled={self.is_enabled}>"
