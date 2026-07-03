"""ReactionService (J1) — like / dislike / favorite toggles + sorted listings.

A toggle inserts or deletes the (unique) reaction row and adjusts the media's
denormalized counter with an atomic SQL ``UPDATE ... SET x = x ± 1`` in the
SAME transaction, so the counters can never drift from the rows. like/dislike
are mutually exclusive (setting one clears the other); favorite is independent.
Everything is tenant-scoped by the guard.
"""
from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.media import Media
from app.models.reaction import REACTION_KINDS, MediaReaction

log = get_logger("reactions")

_COUNTER = {
    "like": Media.like_count,
    "dislike": Media.dislike_count,
    "favorite": Media.favorite_count,
}
_OPPOSITE = {"like": "dislike", "dislike": "like"}


class ReactionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _has(self, media_id: int, user_id: int, kind: str) -> bool:
        row = await self.session.scalar(
            select(MediaReaction.id).where(
                MediaReaction.media_id == media_id,
                MediaReaction.user_id == user_id,
                MediaReaction.kind == kind,
            )
        )
        return row is not None

    async def _remove(self, media_id: int, user_id: int, kind: str) -> bool:
        """Delete the row + decrement the counter (no commit). True if removed."""
        result = await self.session.execute(
            delete(MediaReaction).where(
                MediaReaction.media_id == media_id,
                MediaReaction.user_id == user_id,
                MediaReaction.kind == kind,
            )
        )
        if not result.rowcount:
            return False
        col = _COUNTER[kind]
        await self.session.execute(
            update(Media)
            .where(Media.id == media_id, col > 0)
            .values({col.key: col - 1})
        )
        return True

    async def toggle(self, media_id: int, user_id: int, kind: str) -> bool:
        """Toggle a reaction; returns True when it is now SET, False when cleared.

        Setting like clears an existing dislike (and vice versa). A concurrent
        duplicate insert folds into the unique constraint (no double count).
        """
        if kind not in REACTION_KINDS:
            raise ValueError(f"unknown reaction kind: {kind}")
        media = await self.session.get(Media, media_id)
        if media is None:
            return False

        if await self._has(media_id, user_id, kind):
            await self._remove(media_id, user_id, kind)
            await self.session.commit()
            return False

        # setting like/dislike clears the opposite first (same transaction)
        opposite = _OPPOSITE.get(kind)
        if opposite:
            await self._remove(media_id, user_id, opposite)
        self.session.add(
            MediaReaction(media_id=media_id, user_id=user_id, kind=kind)
        )
        col = _COUNTER[kind]
        await self.session.execute(
            update(Media).where(Media.id == media_id).values({col.key: col + 1})
        )
        try:
            await self.session.commit()
        except IntegrityError:  # concurrent duplicate -> already set, no drift
            await self.session.rollback()
            return True
        return True

    async def user_reactions(self, media_id: int, user_id: int) -> set[str]:
        rows = await self.session.scalars(
            select(MediaReaction.kind).where(
                MediaReaction.media_id == media_id,
                MediaReaction.user_id == user_id,
            )
        )
        return set(rows.all())

    async def favorites(
        self, user_id: int, *, limit: int = 10, offset: int = 0
    ) -> list[Media]:
        """The user's favorited media — only approved + active are shown."""
        rows = await self.session.scalars(
            select(Media)
            .join(MediaReaction, MediaReaction.media_id == Media.id)
            .where(
                MediaReaction.user_id == user_id,
                MediaReaction.kind == "favorite",
                Media.status == "approved",
                Media.is_active.is_(True),
            )
            .order_by(MediaReaction.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all())

    async def listing(
        self, sort: str, *, limit: int = 10, offset: int = 0
    ) -> list[Media]:
        """Public sorted views — approved + active only, tenant-scoped.

        sort ∈ {popular (likes), newest, most_viewed (downloads)}.
        """
        order = {
            "popular": (Media.like_count.desc(), Media.id.desc()),
            "newest": (Media.id.desc(),),
            "most_viewed": (Media.download_count.desc(), Media.id.desc()),
        }.get(sort)
        if order is None:
            order = (Media.id.desc(),)
        rows = await self.session.scalars(
            select(Media)
            .where(Media.status == "approved", Media.is_active.is_(True))
            .order_by(*order)
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all())
