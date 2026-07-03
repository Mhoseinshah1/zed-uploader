"""CommentService (J8) — moderated media comments, tenant-scoped."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import COMMENT_STATUSES, MediaComment


class CommentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, media_id: int, user_id: int, body: str) -> MediaComment:
        comment = MediaComment(
            media_id=media_id, user_id=user_id, body=body.strip()[:2000],
            status="pending",
        )
        self.session.add(comment)
        await self.session.commit()
        return comment

    async def approved_for(self, media_id: int, limit: int = 5) -> list[MediaComment]:
        rows = await self.session.scalars(
            select(MediaComment)
            .where(
                MediaComment.media_id == media_id,
                MediaComment.status == "approved",
            )
            .order_by(MediaComment.id.desc())
            .limit(limit)
        )
        return list(rows.all())

    async def list_by_status(self, status: str) -> list[MediaComment]:
        stmt = select(MediaComment).order_by(MediaComment.id.desc()).limit(200)
        if status in COMMENT_STATUSES:
            stmt = stmt.where(MediaComment.status == status)
        rows = await self.session.scalars(stmt)
        return list(rows.all())

    async def set_status(self, comment_id: int, status: str) -> bool:
        if status not in COMMENT_STATUSES:
            return False
        comment = await self.session.get(MediaComment, comment_id)
        if comment is None:
            return False
        comment.status = status
        await self.session.commit()
        return True

    async def delete(self, comment_id: int) -> bool:
        comment = await self.session.get(MediaComment, comment_id)
        if comment is None:
            return False
        await self.session.delete(comment)
        await self.session.commit()
        return True
