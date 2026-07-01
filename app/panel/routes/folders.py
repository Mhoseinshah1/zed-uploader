"""Folders — list / create / rename / delete (panel)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.services.folder_service import DELETE_HAS_CHILDREN, FolderService

router = APIRouter()


def _p(path: str) -> str:
    return f"{settings.panel_path}{path}"


@router.get("/folders")
async def folders_page(
    request: Request,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    folders = await FolderService(session).list_all()
    names = {f.id: f.name for f in folders}
    return render(request, "folders.html", folders=folders, names=names)


@router.post("/folders/create")
async def folders_create(
    request: Request,
    name: str = Form(...),
    parent_id: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    name = name.strip()
    if name:
        parent = int(parent_id) if parent_id.strip().isdigit() else None
        folder = await FolderService(session).create(name, parent_id=parent)
        if folder is not None:
            await audit(session, request, "folder_create", target=str(folder.id))
    return RedirectResponse(url=_p("/folders"), status_code=302)


@router.post("/folders/{folder_id}/rename")
async def folders_rename(
    request: Request,
    folder_id: int,
    name: str = Form(...),
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if name.strip():
        await FolderService(session).rename(folder_id, name)
        await audit(session, request, "folder_rename", target=str(folder_id))
    return RedirectResponse(url=_p("/folders"), status_code=302)


@router.post("/folders/{folder_id}/delete")
async def folders_delete(
    request: Request,
    folder_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    result = await FolderService(session).delete(folder_id)
    if result == DELETE_HAS_CHILDREN:
        return RedirectResponse(url=_p("/folders?error=has_children"), status_code=302)
    await audit(session, request, "folder_delete", target=str(folder_id))
    return RedirectResponse(url=_p("/folders"), status_code=302)
