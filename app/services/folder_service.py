"""FolderService — create / rename / delete / list folders (and subfolders).

Deleting a folder is blocked while it still has subfolders (the admin must
empty or delete them first); its media are never deleted — the ``media.folder_id``
FK is ``ON DELETE SET NULL`` so they become uncategorised.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.folder import Folder

# delete() outcomes
DELETE_OK = "ok"
DELETE_HAS_CHILDREN = "has_children"
DELETE_NOT_FOUND = "not_found"


class FolderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, folder_id: int) -> Folder | None:
        return await self.session.scalar(select(Folder).where(Folder.id == folder_id))

    async def create(
        self, name: str, *, parent_id: int | None = None, owner_admin_id: int | None = None
    ) -> Folder | None:
        """Create a folder. Returns None if a given parent does not exist."""
        if parent_id is not None and await self.get(parent_id) is None:
            return None
        folder = Folder(
            name=name.strip(), parent_id=parent_id, owner_admin_id=owner_admin_id
        )
        self.session.add(folder)
        await self.session.commit()
        return folder

    async def rename(self, folder_id: int, name: str) -> bool:
        folder = await self.get(folder_id)
        if folder is None:
            return False
        folder.name = name.strip()
        await self.session.commit()
        return True

    async def has_children(self, folder_id: int) -> bool:
        found = await self.session.scalar(
            select(Folder.id).where(Folder.parent_id == folder_id).limit(1)
        )
        return found is not None

    async def delete(self, folder_id: int) -> str:
        """Delete a folder unless it has subfolders. Media are nulled by the FK."""
        folder = await self.get(folder_id)
        if folder is None:
            return DELETE_NOT_FOUND
        if await self.has_children(folder_id):
            return DELETE_HAS_CHILDREN
        await self.session.delete(folder)
        await self.session.commit()
        return DELETE_OK

    async def list_children(
        self, parent_id: int | None = None, *, include_inactive: bool = True
    ) -> list[Folder]:
        stmt = select(Folder).where(
            Folder.parent_id.is_(None) if parent_id is None else Folder.parent_id == parent_id
        )
        if not include_inactive:
            stmt = stmt.where(Folder.is_active.is_(True))
        stmt = stmt.order_by(Folder.sort_order, Folder.id)
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def list_all(self) -> list[Folder]:
        result = await self.session.scalars(
            select(Folder).order_by(Folder.parent_id.nulls_first(), Folder.sort_order, Folder.id)
        )
        return list(result.all())

    async def count_all(self) -> int:
        return int(await self.session.scalar(select(func.count(Folder.id))) or 0)
