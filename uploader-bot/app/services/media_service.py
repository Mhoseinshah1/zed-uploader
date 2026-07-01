"""Media service.

Contains the three critical, race-free download methods (Section 6.1): the
download counter is incremented with a single conditional
``UPDATE ... RETURNING`` so concurrent claims can never exceed ``download_limit``.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.download_log import DownloadLog
from app.models.media import Media
from app.models.media_file import MediaFile
from app.services.code_generator import generate_unique_code


class MediaStatus(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    INACTIVE = "inactive"
    LIMIT_REACHED = "limit_reached"


class MediaService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # reads / creation
    # ------------------------------------------------------------------
    async def get_by_code(self, code: str) -> Media | None:
        return await self.session.scalar(select(Media).where(Media.code == code))

    async def create_media(
        self,
        *,
        files: list[dict[str, Any]],
        owner_user_id: int | None = None,
        title: str | None = None,
        caption: str | None = None,
        protect_content: bool | None = None,
        auto_delete_seconds: int | None = None,
        download_limit: int | None = None,
        password_hash: str | None = None,
    ) -> Media:
        """Create a Media row plus its MediaFile children in one transaction."""
        code = await generate_unique_code(self.session)
        media = Media(
            code=code,
            owner_user_id=owner_user_id,
            title=title,
            caption=caption,
            password_hash=password_hash,
            download_limit=download_limit,
            protect_content=(
                settings.default_protect_content
                if protect_content is None
                else protect_content
            ),
            auto_delete_seconds=(
                (settings.default_auto_delete_seconds or None)
                if auto_delete_seconds is None
                else auto_delete_seconds
            ),
        )
        for index, file_data in enumerate(files):
            media.files.append(MediaFile(sort_order=index, **file_data))
        self.session.add(media)
        await self.session.commit()
        return media

    def deep_link(self, media: Media) -> str:
        """Build the t.me deep link for a media item."""
        return f"https://t.me/{settings.bot_username}?start={media.code}"

    async def check_status(self, code: str) -> MediaStatus:
        """Non-mutating status check (does not claim a download slot)."""
        media = await self.get_by_code(code)
        if media is None:
            return MediaStatus.NOT_FOUND
        if not media.is_active:
            return MediaStatus.INACTIVE
        if (
            media.download_limit is not None
            and media.download_count >= media.download_limit
        ):
            return MediaStatus.LIMIT_REACHED
        return MediaStatus.OK

    # ------------------------------------------------------------------
    # Section 6.1 — atomic download claim (no race on download_limit)
    # ------------------------------------------------------------------
    async def try_claim_download(self, code: str) -> tuple[MediaStatus, Media | None]:
        media = await self.get_by_code(code)
        if media is None:
            return MediaStatus.NOT_FOUND, None
        if not media.is_active:
            return MediaStatus.INACTIVE, media
        stmt = (
            update(Media)
            .where(Media.id == media.id, Media.is_active.is_(True))
            .where(
                (Media.download_limit.is_(None))
                | (Media.download_count < Media.download_limit)
            )
            .values(download_count=Media.download_count + 1)
            .returning(Media.download_count)
        )
        new_count = (await self.session.execute(stmt)).scalar_one_or_none()
        await self.session.commit()
        if new_count is None:
            return MediaStatus.LIMIT_REACHED, media
        return MediaStatus.OK, media

    async def release_download(self, media_id: int) -> None:
        await self.session.execute(
            update(Media)
            .where(Media.id == media_id, Media.download_count > 0)
            .values(download_count=Media.download_count - 1)
        )
        await self.session.commit()

    async def log_download(
        self, media_id: int, *, telegram_id: int, user_id: int | None
    ) -> None:
        self.session.add(
            DownloadLog(media_id=media_id, user_id=user_id, telegram_id=telegram_id)
        )
        await self.session.commit()

    # ------------------------------------------------------------------
    # stats / listing helpers
    # ------------------------------------------------------------------
    async def list_media(self, *, limit: int = 50, offset: int = 0) -> list[Media]:
        result = await self.session.scalars(
            select(Media).order_by(Media.id.desc()).limit(limit).offset(offset)
        )
        return list(result.all())

    async def count_media(self) -> int:
        return int(await self.session.scalar(select(func.count(Media.id))) or 0)

    async def total_downloads(self) -> int:
        return int(await self.session.scalar(select(func.count(DownloadLog.id))) or 0)
