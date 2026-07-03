"""CustomButton (J8) — tenant-defined reply-keyboard buttons.

type ∈ {url, message, action}. ``action`` values are restricted to a code-side
whitelist (see custom_button_service.ACTION_WHITELIST) — never arbitrary code.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped

BUTTON_TYPES = ("url", "message", "action")


class CustomButton(TenantScoped, Base):
    __tablename__ = "custom_buttons"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # url|message|action
    value: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("true"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<CustomButton {self.label!r} {self.type!r}>"
