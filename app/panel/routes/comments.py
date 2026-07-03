"""Comment moderation (J8): list by status, approve / reject / delete."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.models.comment import COMMENT_STATUSES
from app.models.media import Media
from app.panel.deps import audit, render, require_role, verify_csrf
from app.services.comment_service import CommentService

router = APIRouter()


def _p(suffix: str = "") -> str:
    return f"{settings.panel_path}/comments{suffix}"


@router.get("/comments")
async def comments_list(
    request: Request,
    status: str = "pending",
    _=Depends(require_role("owner", "admin", "content")),
    session: AsyncSession = Depends(get_session),
):
    status = status if status in COMMENT_STATUSES else "pending"
    items = await CommentService(session).list_by_status(status)
    media_codes: dict[int, str] = {}
    if items:
        rows = await session.execute(
            select(Media.id, Media.code).where(
                Media.id.in_({c.media_id for c in items})
            )
        )
        media_codes = {mid: code for mid, code in rows.all()}
    return render(
        request, "comments.html",
        items=items, status=status, statuses=COMMENT_STATUSES,
        media_codes=media_codes,
    )


@router.post("/comments/{comment_id}/status")
async def comment_set_status(
    request: Request,
    comment_id: int,
    status: str = Form(...),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner", "admin", "content")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if await CommentService(session).set_status(comment_id, status):
        await audit(session, request, "comment_status", target=f"{comment_id}:{status}")
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/comments/{comment_id}/delete")
async def comment_delete(
    request: Request,
    comment_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_role("owner", "admin", "content")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if await CommentService(session).delete(comment_id):
        await audit(session, request, "comment_delete", target=str(comment_id))
    return RedirectResponse(url=_p(), status_code=302)
