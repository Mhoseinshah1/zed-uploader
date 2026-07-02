"""BotCommandEntry — one editable entry of the Telegram command menu.

Two scopes: 'user' (the default menu everyone sees) and 'admin' (pushed
per-admin chat, since Telegram has no built-in bot-admin scope). When a scope
has no rows at all, the built-in defaults in bot_command_service apply.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BotCommandEntry(Base):
    __tablename__ = "bot_commands"
    __table_args__ = (
        UniqueConstraint("scope", "command", name="uq_bot_commands_scope_command"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(8), nullable=False)  # user | admin
    command: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(String(256), nullable=False)
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
        return f"<BotCommandEntry {self.scope}:/{self.command} active={self.is_active}>"
