"""Media service.

Contains the three critical, race-free download methods (Section 6.1): the
download counter is incremented with a single conditional
``UPDATE ... RETURNING`` so concurrent claims can never exceed ``download_limit``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_media_password, verify_media_password
from app.core.tenant_context import PLATFORM_TENANT_ID, current_tenant
from app.models.download_log import DownloadLog
from app.models.media import Media
from app.models.media_file import MediaFile
from app.models.tenant import Tenant
from app.models.user import User
from app.services.code_generator import generate_unique_code


class MediaStatus(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    INACTIVE = "inactive"
    LIMIT_REACHED = "limit_reached"


# review lifecycle for a media item (B1)
APPROVED = "approved"
PENDING = "pending"
REJECTED = "rejected"
DRAFT = "draft"


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
        status: str = APPROVED,
    ) -> Media:
        """Create a Media row plus its MediaFile children in one transaction."""
        code = await generate_unique_code(self.session)
        media = Media(
            code=code,
            owner_user_id=owner_user_id,
            title=title,
            caption=caption,
            password_hash=password_hash,
            status=status,
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

    async def deep_link(self, media: Media) -> str:
        """Build the t.me deep link using the CURRENT tenant's own bot username.

        Each tenant's files must link at that tenant's bot, not the platform's.
        The platform tenant (id 1) uses the env BOT_USERNAME (its tenant row
        carries only the placeholder 'platform'); customer tenants use the
        username captured from getMe at creation. Cached per service instance
        (one tenant per request)."""
        return f"https://t.me/{await self._bot_username()}?start={media.code}"

    async def _bot_username(self) -> str:
        cached = getattr(self, "_bot_username_cache", None)
        if cached is not None:
            return cached
        tid = current_tenant()
        username = settings.bot_username
        if isinstance(tid, int) and tid != PLATFORM_TENANT_ID:
            row = await self.session.scalar(
                select(Tenant.bot_username).where(Tenant.id == tid)
            )
            username = row or settings.bot_username
        self._bot_username_cache = username
        return username

    async def check_status(self, code: str) -> MediaStatus:
        """Non-mutating status check (does not claim a download slot)."""
        media = await self.get_by_code(code)
        if media is None:
            return MediaStatus.NOT_FOUND
        # Only approved media are retrievable by code. A pending/rejected/draft
        # item must be indistinguishable from a non-existent one.
        if media.status != APPROVED:
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
        if media.status != APPROVED:
            return MediaStatus.NOT_FOUND, None
        if not media.is_active:
            return MediaStatus.INACTIVE, media
        # The status guard is repeated in the atomic UPDATE so a status change
        # racing the claim can never hand out a non-approved item.
        stmt = (
            update(Media)
            .where(
                Media.id == media.id,
                Media.is_active.is_(True),
                Media.status == APPROVED,
            )
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

    # ------------------------------------------------------------------
    # owner-scoped admin-panel methods (Phase 2)
    #
    # Every mutator's WHERE includes owner_user_id, so a wrong owner is a
    # no-op that returns False. Each commits per call.
    # ------------------------------------------------------------------
    async def list_by_owner(
        self, owner_user_id: int, *, limit: int = 5, offset: int = 0
    ) -> list[Media]:
        result = await self.session.scalars(
            select(Media)
            .where(Media.owner_user_id == owner_user_id)
            .order_by(Media.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.all())

    async def count_by_owner(self, owner_user_id: int) -> int:
        return int(
            await self.session.scalar(
                select(func.count(Media.id)).where(
                    Media.owner_user_id == owner_user_id
                )
            )
            or 0
        )

    async def count_quota_by_owner(self, owner_user_id: int) -> int:
        """Media occupying a plan quota slot for an owner: everything except
        rejected items (approved + pending + draft)."""
        return int(
            await self.session.scalar(
                select(func.count(Media.id)).where(
                    Media.owner_user_id == owner_user_id,
                    Media.status != REJECTED,
                )
            )
            or 0
        )

    async def get_owned(self, media_id: int, owner_user_id: int) -> Media | None:
        return await self.session.scalar(
            select(Media).where(
                Media.id == media_id, Media.owner_user_id == owner_user_id
            )
        )

    async def _owned_update(self, media_id: int, owner_user_id: int, **values: Any) -> bool:
        result = await self.session.execute(
            update(Media)
            .where(Media.id == media_id, Media.owner_user_id == owner_user_id)
            .values(**values)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def set_active(
        self, media_id: int, owner_user_id: int, is_active: bool
    ) -> bool:
        return await self._owned_update(media_id, owner_user_id, is_active=is_active)

    async def set_protect(
        self, media_id: int, owner_user_id: int, protect: bool
    ) -> bool:
        return await self._owned_update(
            media_id, owner_user_id, protect_content=protect
        )

    async def set_auto_delete(
        self, media_id: int, owner_user_id: int, seconds: int | None
    ) -> bool:
        return await self._owned_update(
            media_id, owner_user_id, auto_delete_seconds=seconds
        )

    async def set_download_limit(
        self, media_id: int, owner_user_id: int, limit: int | None
    ) -> bool:
        return await self._owned_update(
            media_id, owner_user_id, download_limit=limit
        )

    async def set_caption(
        self, media_id: int, owner_user_id: int, caption: str | None
    ) -> bool:
        return await self._owned_update(media_id, owner_user_id, caption=caption)

    async def set_folder(
        self, media_id: int, owner_user_id: int, folder_id: int | None
    ) -> bool:
        """Move an owned media into a folder (None = uncategorised).

        A non-null folder_id must reference an existing folder, else this is a
        no-op returning False (avoids a foreign-key violation).
        """
        if folder_id is not None:
            from app.models.folder import Folder

            exists = await self.session.scalar(
                select(Folder.id).where(Folder.id == folder_id)
            )
            if exists is None:
                return False
        return await self._owned_update(media_id, owner_user_id, folder_id=folder_id)

    async def list_by_folder(
        self, folder_id: int | None, owner_user_id: int, *, limit: int = 5, offset: int = 0
    ) -> list[Media]:
        """Owner's media in a folder (None = uncategorised). All statuses — this
        is the owner's own view, not a public listing."""
        result = await self.session.scalars(
            select(Media)
            .where(
                Media.owner_user_id == owner_user_id,
                Media.folder_id.is_(None) if folder_id is None else Media.folder_id == folder_id,
            )
            .order_by(Media.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.all())

    async def count_by_folder(self, folder_id: int | None, owner_user_id: int) -> int:
        return int(
            await self.session.scalar(
                select(func.count(Media.id)).where(
                    Media.owner_user_id == owner_user_id,
                    Media.folder_id.is_(None)
                    if folder_id is None
                    else Media.folder_id == folder_id,
                )
            )
            or 0
        )

    # ------------------------------------------------------------------
    # per-file password (bcrypt; the raw password is never stored)
    # ------------------------------------------------------------------
    async def set_password(
        self, media_id: int, owner_user_id: int, raw_password: str
    ) -> bool:
        return await self._owned_update(
            media_id, owner_user_id, password_hash=hash_media_password(raw_password)
        )

    async def clear_password(self, media_id: int, owner_user_id: int) -> bool:
        return await self._owned_update(media_id, owner_user_id, password_hash=None)

    @staticmethod
    def verify_password(media: Media, raw_password: str) -> bool:
        """True if the file has no password, or the given one matches."""
        if not media.password_hash:
            return True
        return verify_media_password(raw_password, media.password_hash)

    # ------------------------------------------------------------------
    # review queue (B1) — admin-facing, NOT owner-scoped
    # ------------------------------------------------------------------
    async def list_pending(self, *, limit: int = 5, offset: int = 0) -> list[Media]:
        result = await self.session.scalars(
            select(Media)
            .where(Media.status == PENDING)
            .order_by(Media.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.all())

    async def count_pending(self) -> int:
        return int(
            await self.session.scalar(
                select(func.count(Media.id)).where(Media.status == PENDING)
            )
            or 0
        )

    async def get_pending(self, media_id: int) -> Media | None:
        return await self.session.scalar(
            select(Media).where(Media.id == media_id, Media.status == PENDING)
        )

    async def approve(self, media_id: int, admin_id: int | None) -> Media | None:
        """Approve a pending media. Returns the media, or None if not pending."""
        media = await self.get_pending(media_id)
        if media is None:
            return None
        media.status = APPROVED
        media.approved_at = datetime.now(timezone.utc)
        media.reviewed_by_admin_id = admin_id
        await self.session.commit()
        return media

    async def reject(
        self, media_id: int, admin_id: int | None, note: str | None = None
    ) -> Media | None:
        """Reject a pending media. Returns the media, or None if not pending."""
        media = await self.get_pending(media_id)
        if media is None:
            return None
        media.status = REJECTED
        media.review_note = note
        media.reviewed_by_admin_id = admin_id
        await self.session.commit()
        return media

    async def owner_telegram_id(self, owner_user_id: int | None) -> int | None:
        if owner_user_id is None:
            return None
        return await self.session.scalar(
            select(User.telegram_id).where(User.id == owner_user_id)
        )

    # ------------------------------------------------------------------
    # search (B3) — bounded, ILIKE-escaped, optionally approved-only
    # ------------------------------------------------------------------
    MAX_SEARCH_LIMIT = 50

    @staticmethod
    def _escape_like(term: str) -> str:
        """Escape LIKE wildcards so a user '%'/'_' is literal (no full scan)."""
        return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    async def search(
        self,
        query: str,
        *,
        owner_user_id: int | None = None,
        approved_only: bool = False,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[Media], int]:
        """Search media by code/title/caption/file_name.

        - ``owner_user_id`` scopes to one owner (admin searching their own,
          any status); ``approved_only`` restricts to approved+active (public).
          The two combine (owner AND approved) when both are given.
        - Always bounded by ``limit`` (capped) + ``offset``; returns (items, total).
        """
        q = query.strip()
        if not q:
            return [], 0
        limit = max(1, min(limit, self.MAX_SEARCH_LIMIT))
        pattern = f"%{self._escape_like(q)}%"
        term = or_(
            Media.code.ilike(pattern, escape="\\"),
            Media.title.ilike(pattern, escape="\\"),
            Media.caption.ilike(pattern, escape="\\"),
            Media.id.in_(
                select(MediaFile.media_id).where(
                    MediaFile.file_name.ilike(pattern, escape="\\")
                )
            ),
        )
        stmt = select(Media).where(term)
        if owner_user_id is not None:
            stmt = stmt.where(Media.owner_user_id == owner_user_id)
        if approved_only:
            stmt = stmt.where(Media.status == APPROVED, Media.is_active.is_(True))
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(stmt.subquery())
            )
            or 0
        )
        rows = await self.session.scalars(
            stmt.order_by(Media.id.desc()).limit(limit).offset(offset)
        )
        return list(rows.all()), total

    async def delete_media(self, media_id: int, owner_user_id: int) -> bool:
        result = await self.session.execute(
            delete(Media).where(
                Media.id == media_id, Media.owner_user_id == owner_user_id
            )
        )
        await self.session.commit()
        return result.rowcount > 0

    async def owner_stats(self, owner_user_id: int) -> tuple[int, int]:
        """Return (media_count, total_downloads) for one owner."""
        media_count = await self.count_by_owner(owner_user_id)
        total = await self.session.scalar(
            select(func.count(DownloadLog.id))
            .join(Media, DownloadLog.media_id == Media.id)
            .where(Media.owner_user_id == owner_user_id)
        )
        return media_count, int(total or 0)
