"""Media model — one logical uploaded item, addressable by a short ``code``.

A Media groups one or more :class:`MediaFile` rows (an album / multi-file item).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TenantScoped

if TYPE_CHECKING:
    from app.models.media_file import MediaFile


class Media(TenantScoped, Base):
    __tablename__ = "media"
    # B3: trigram GIN indexes make substring ILIKE search on the free-text
    # fields fast. F1: the short code is unique PER TENANT (two bots may hand
    # out the same code without colliding).
    __table_args__ = (
        Index("uq_media_tenant_code", "tenant_id", "code", unique=True),
        Index(
            "ix_media_title_trgm", "title",
            postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index(
            "ix_media_caption_trgm", "caption",
            postgresql_using="gin", postgresql_ops={"caption": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # B1 review workflow: default 'approved' so existing rows + admin uploads
    # stay live; user uploads may be 'pending' until an admin approves them.
    status: Mapped[str] = mapped_column(
        String(16),
        default="approved",
        server_default=text("'approved'"),
        index=True,
        nullable=False,
    )  # draft | pending | approved | rejected
    reviewed_by_admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # B2: optional folder grouping. ON DELETE SET NULL -> media survive a folder
    # deletion (they become uncategorised).
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), index=True, nullable=True
    )
    download_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_count: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    protect_content: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    auto_delete_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    files: Mapped[list["MediaFile"]] = relationship(
        back_populates="media",
        cascade="all, delete-orphan",
        order_by="MediaFile.sort_order",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Media id={self.id} code={self.code!r}>"
