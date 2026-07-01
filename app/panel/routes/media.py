"""Media — list/search/detail, toggle active, delete (reuses MediaService)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_media_password
from app.db.session import get_session
from app.models.download_log import DownloadLog
from app.models.media import Media
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.services.folder_service import FolderService

router = APIRouter()
PAGE_SIZE = 20


@router.get("/media")
async def media_list(
    request: Request,
    q: str = "",
    folder_id: str = "",
    page: int = 0,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Media)
    q = q.strip()
    if q:
        stmt = stmt.where(Media.code.ilike(f"%{q}%"))
    folder_val = int(folder_id) if folder_id.strip().isdigit() else None
    if folder_val is not None:
        stmt = stmt.where(Media.folder_id == folder_val)
    total = int(await session.scalar(select(func.count()).select_from(stmt.subquery())))
    page = max(0, page)
    rows = list(
        await session.scalars(
            stmt.order_by(Media.id.desc()).limit(PAGE_SIZE).offset(page * PAGE_SIZE)
        )
    )
    folders = await FolderService(session).list_all()
    return render(
        request, "media.html", media=rows, q=q, page=page, total=total,
        page_size=PAGE_SIZE, folders=folders, folder_id=folder_val,
    )


@router.get("/media/{media_id}")
async def media_detail(
    request: Request,
    media_id: int,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    media = await session.scalar(select(Media).where(Media.id == media_id))
    if media is None:
        return RedirectResponse(url=f"{settings.panel_path}/media", status_code=302)
    logs = list(
        await session.scalars(
            select(DownloadLog)
            .where(DownloadLog.media_id == media_id)
            .order_by(DownloadLog.id.desc())
            .limit(20)
        )
    )
    folders = await FolderService(session).list_all()
    return render(request, "media_detail.html", media=media, logs=logs, folders=folders)


@router.post("/media/{media_id}/folder")
async def media_move_folder(
    request: Request,
    media_id: int,
    folder_id: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    """Move a media into a folder (empty = uncategorised). Panel admins can move
    any media, so this is not owner-scoped."""
    await verify_csrf(request)
    media = await session.scalar(select(Media).where(Media.id == media_id))
    if media is not None:
        target = int(folder_id) if folder_id.strip().isdigit() else None
        if target is None or await FolderService(session).get(target) is not None:
            media.folder_id = target
            await session.commit()
            await audit(session, request, "media_move_folder", target=str(media_id))
    return RedirectResponse(
        url=f"{settings.panel_path}/media/{media_id}", status_code=302
    )


@router.post("/media/{media_id}/toggle")
async def media_toggle(
    request: Request,
    media_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    media = await session.scalar(select(Media).where(Media.id == media_id))
    if media is not None:
        media.is_active = not media.is_active
        await session.commit()
        await audit(session, request, "media_toggle", target=str(media_id))
    return RedirectResponse(
        url=f"{settings.panel_path}/media/{media_id}", status_code=302
    )


@router.post("/media/{media_id}/password")
async def media_password(
    request: Request,
    media_id: int,
    password: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    """Set/change (non-empty) or remove (empty) a media's password."""
    await verify_csrf(request)
    media = await session.scalar(select(Media).where(Media.id == media_id))
    if media is not None:
        pw = password.strip()
        media.password_hash = hash_media_password(pw) if pw else None
        await session.commit()
        await audit(
            session,
            request,
            "media_password_set" if pw else "media_password_clear",
            target=str(media_id),
        )
    return RedirectResponse(
        url=f"{settings.panel_path}/media/{media_id}", status_code=302
    )


@router.post("/media/{media_id}/delete")
async def media_delete(
    request: Request,
    media_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    media = await session.scalar(select(Media).where(Media.id == media_id))
    if media is not None:
        await session.delete(media)
        await session.commit()
        await audit(session, request, "media_delete", target=str(media_id))
    return RedirectResponse(url=f"{settings.panel_path}/media", status_code=302)
