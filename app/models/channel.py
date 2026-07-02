"""RequiredChannel model — channels a user must join before retrieving files."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TenantScoped


class RequiredChannel(TenantScoped, Base):
    __tablename__ = "required_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stores a public "@username" or a numeric "-100..." id.
    chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invite_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<RequiredChannel id={self.id} chat_id={self.chat_id!r}>"
