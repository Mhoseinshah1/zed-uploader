"""Versioned admin REST API (D4): /api/v1 — JWT or panel-session auth.

- Auth: POST /api/v1/auth/login (panel username/password) -> HS256 JWT;
  every other route accepts `Authorization: Bearer <jwt>` OR a valid panel
  session cookie. The legacy read-only X-API-Key endpoints stay untouched.
- All list endpoints share limit/offset pagination (limit capped at 100) plus
  per-resource filters; every route carries the shared per-IP rate limit.
- Responses use explicit schemas: password hashes, media password hashes and
  gateway credentials are never serialized.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession, rate_limit
from app.core import jwt_utils
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.db.session import get_session
from app.models.ad import PLACEMENTS, Ad
from app.models.backup_job import BackupJob
from app.models.broadcast import BroadcastJob
from app.models.channel import RequiredChannel
from app.models.folder import Folder
from app.models.media import Media
from app.models.panel import PanelUser
from app.models.payment import Payment
from app.models.plan import Plan
from app.models.user import User
from app.panel import security as panel_security
from app.panel.session import COOKIE_NAME, SessionStore
from app.services import broadcast as broadcast_service
from app.services.ad_service import AdService
from app.services.backup_service import BackupService
from app.services.channel_service import ChannelService
from app.services.folder_service import DELETE_HAS_CHILDREN, FolderService
from app.services.payment_service import PaymentService

log = get_logger("api.v1")

router = APIRouter(prefix="/api/v1", tags=["admin-v1"], dependencies=[Depends(rate_limit)])

MAX_LIMIT = 100


def _page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, MAX_LIMIT)), max(0, offset)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
async def _resolve_api_user(request: Request, session: AsyncSession) -> PanelUser | None:
    """Bearer JWT else panel session -> the authenticated PanelUser (or None)."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        payload = jwt_utils.decode(auth[7:].strip())
        if payload and payload.get("sub"):
            return await session.scalar(
                select(PanelUser).where(
                    PanelUser.id == int(payload["sub"]),
                    PanelUser.is_active.is_(True),
                )
            )
        return None
    sid = panel_security.unsign(request.cookies.get(COOKIE_NAME))
    data = await SessionStore(get_redis()).get(sid) if sid else None
    if data and data.get("uid"):
        return await session.scalar(
            select(PanelUser).where(
                PanelUser.id == data["uid"], PanelUser.is_active.is_(True)
            )
        )
    return None


async def require_admin_api(
    request: Request, session: AsyncSession = Depends(get_session)
):
    """Admin identity for the v1 API: Bearer JWT, else the panel session.

    Generator dependency (Fix-3): after authenticating, it BINDS the tenant
    context from ``PanelUser.tenant_id`` for the whole request, so every /api/v1
    query is automatically scoped to the caller's tenant by the F1 layer — a
    request can never read another tenant's data. Yields the panel-user id.
    """
    user = await _resolve_api_user(request, session)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    from app.core.tenant_context import reset_tenant, set_tenant

    token = set_tenant(user.tenant_id)
    try:
        yield user.id
    finally:
        reset_tenant(token)


AdminId = Depends(require_admin_api)


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
async def login(body: LoginIn, session: DbSession):
    user = await session.scalar(
        select(PanelUser).where(
            PanelUser.username == body.username.strip().lower(),
            PanelUser.is_active.is_(True),
        )
    )
    if user is None or not panel_security.verify_password(
        body.password, user.password_hash
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad credentials")
    return {"access_token": jwt_utils.encode(user.id), "token_type": "bearer"}


# ---------------------------------------------------------------------------
# schemas (never expose hashes/credentials)
# ---------------------------------------------------------------------------
class MediaOut(BaseModel):
    id: int
    code: str
    title: str | None
    caption: str | None
    status: str
    is_active: bool
    protect_content: bool
    has_password: bool
    folder_id: int | None
    download_count: int
    download_limit: int | None
    owner_user_id: int | None
    created_at: datetime

    @classmethod
    def of(cls, m: Media) -> "MediaOut":
        return cls(
            id=m.id, code=m.code, title=m.title, caption=m.caption,
            status=m.status, is_active=m.is_active, protect_content=m.protect_content,
            has_password=m.password_hash is not None, folder_id=m.folder_id,
            download_count=m.download_count, download_limit=m.download_limit,
            owner_user_id=m.owner_user_id, created_at=m.created_at,
        )


class UserOut(BaseModel):
    id: int
    telegram_id: int
    username: str | None
    first_name: str | None
    is_blocked: bool
    plan: str
    balance: int
    created_at: datetime


class PageOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list


# ---------------------------------------------------------------------------
# media
# ---------------------------------------------------------------------------
@router.get("/media")
async def media_list(
    session: DbSession,
    _admin: int = AdminId,
    q: str = "",
    status_f: str = Query("", alias="status"),
    folder_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
):
    limit, offset = _page(limit, offset)
    stmt = select(Media)
    if q.strip():
        stmt = stmt.where(Media.code.ilike(f"%{q.strip()}%"))
    if status_f:
        stmt = stmt.where(Media.status == status_f)
    if folder_id is not None:
        stmt = stmt.where(Media.folder_id == folder_id)
    total = int(await session.scalar(select(func.count()).select_from(stmt.subquery())))
    rows = await session.scalars(
        stmt.order_by(Media.id.desc()).limit(limit).offset(offset)
    )
    return PageOut(
        total=total, limit=limit, offset=offset,
        items=[MediaOut.of(m) for m in rows],
    )


class MediaPatch(BaseModel):
    is_active: bool | None = None
    status: str | None = Field(default=None, pattern="^(pending|approved|rejected)$")
    caption: str | None = None
    folder_id: int | None = None
    clear_folder: bool = False


@router.patch("/media/{media_id}")
async def media_patch(media_id: int, body: MediaPatch, session: DbSession, _admin: int = AdminId):
    media = await session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "media not found")
    if body.is_active is not None:
        media.is_active = body.is_active
    if body.status is not None:
        media.status = body.status
    if body.caption is not None:
        media.caption = body.caption or None
    if body.clear_folder:
        media.folder_id = None
    elif body.folder_id is not None:
        if await session.get(Folder, body.folder_id) is None:
            raise HTTPException(400, "folder does not exist")
        media.folder_id = body.folder_id
    await session.commit()
    return MediaOut.of(media)


@router.delete("/media/{media_id}")
async def media_delete(media_id: int, session: DbSession, _admin: int = AdminId):
    media = await session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "media not found")
    await session.delete(media)
    await session.commit()
    return {"deleted": media_id}


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
@router.get("/users")
async def users_list(
    session: DbSession,
    _admin: int = AdminId,
    q: str = "",
    blocked: bool | None = None,
    limit: int = 20,
    offset: int = 0,
):
    limit, offset = _page(limit, offset)
    stmt = select(User)
    if q.strip():
        stmt = stmt.where(User.username.ilike(f"%{q.strip()}%"))
    if blocked is not None:
        stmt = stmt.where(User.is_blocked.is_(blocked))
    total = int(await session.scalar(select(func.count()).select_from(stmt.subquery())))
    rows = await session.scalars(
        stmt.order_by(User.id.desc()).limit(limit).offset(offset)
    )
    return PageOut(
        total=total, limit=limit, offset=offset,
        items=[UserOut.model_validate(u, from_attributes=True) for u in rows],
    )


class UserPatch(BaseModel):
    is_blocked: bool


@router.patch("/users/{user_id}")
async def users_patch(user_id: int, body: UserPatch, session: DbSession, _admin: int = AdminId):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    user.is_blocked = body.is_blocked
    await session.commit()
    return UserOut.model_validate(user, from_attributes=True)


# ---------------------------------------------------------------------------
# folders
# ---------------------------------------------------------------------------
class FolderIn(BaseModel):
    name: str
    parent_id: int | None = None


@router.get("/folders")
async def folders_list(session: DbSession, _admin: int = AdminId, limit: int = 100, offset: int = 0):
    limit, offset = _page(limit, offset)
    rows = await FolderService(session).list_all()
    items = [
        {"id": f.id, "name": f.name, "parent_id": f.parent_id, "is_active": f.is_active}
        for f in rows[offset:offset + limit]
    ]
    return PageOut(total=len(rows), limit=limit, offset=offset, items=items)


@router.post("/folders", status_code=201)
async def folders_create(body: FolderIn, session: DbSession, _admin: int = AdminId):
    if not body.name.strip():
        raise HTTPException(400, "name required")
    folder = await FolderService(session).create(body.name, parent_id=body.parent_id)
    if folder is None:
        raise HTTPException(400, "parent does not exist")
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id}


@router.delete("/folders/{folder_id}")
async def folders_delete(folder_id: int, session: DbSession, _admin: int = AdminId):
    result = await FolderService(session).delete(folder_id)
    if result == DELETE_HAS_CHILDREN:
        raise HTTPException(409, "folder has subfolders")
    if result != "ok":
        raise HTTPException(404, "folder not found")
    return {"deleted": folder_id}


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------
class PlanPatch(BaseModel):
    price: int | None = Field(default=None, ge=0)
    duration_days: int | None = Field(default=None, ge=0)
    max_files: int | None = None
    stars_price: int | None = None
    is_active: bool | None = None


@router.get("/plans")
async def plans_list(session: DbSession, _admin: int = AdminId):
    rows = await session.scalars(select(Plan).order_by(Plan.id).limit(MAX_LIMIT))
    return {
        "items": [
            {
                "key": p.key, "title": p.title, "price": p.price,
                "duration_days": p.duration_days, "max_files": p.max_files,
                "stars_price": p.stars_price, "is_active": p.is_active,
            }
            for p in rows
        ]
    }


@router.patch("/plans/{key}")
async def plans_patch(key: str, body: PlanPatch, session: DbSession, _admin: int = AdminId):
    plan = await session.scalar(select(Plan).where(Plan.key == key))
    if plan is None:
        raise HTTPException(404, "plan not found")
    for field in ("price", "duration_days", "max_files", "stars_price", "is_active"):
        value = getattr(body, field)
        if value is not None:
            setattr(plan, field, value)
    await session.commit()
    return {"key": plan.key, "price": plan.price, "is_active": plan.is_active}


# ---------------------------------------------------------------------------
# ads
# ---------------------------------------------------------------------------
class AdIn(BaseModel):
    title: str
    text: str
    placement: str
    button_text: str | None = None
    button_url: str | None = None
    target_plan: str | None = None
    impression_limit: int | None = Field(default=None, ge=1)


@router.get("/ads")
async def ads_list(session: DbSession, _admin: int = AdminId, limit: int = 50, offset: int = 0):
    limit, offset = _page(limit, offset)
    rows = await AdService(session).list_all(limit=MAX_LIMIT)
    items = [
        {
            "id": a.id, "title": a.title, "placement": a.placement,
            "is_active": a.is_active, "impression_count": a.impression_count,
            "click_count": a.click_count,
        }
        for a in rows[offset:offset + limit]
    ]
    return PageOut(total=len(rows), limit=limit, offset=offset, items=items)


@router.post("/ads", status_code=201)
async def ads_create(body: AdIn, session: DbSession, _admin: int = AdminId):
    if body.placement not in PLACEMENTS:
        raise HTTPException(400, "bad placement")
    ad = await AdService(session).create(
        title=body.title, text=body.text, placement=body.placement,
        button_text=body.button_text, button_url=body.button_url,
        target_plan=body.target_plan, impression_limit=body.impression_limit,
    )
    return {"id": ad.id}


@router.delete("/ads/{ad_id}")
async def ads_delete(ad_id: int, session: DbSession, _admin: int = AdminId):
    if not await AdService(session).delete(ad_id):
        raise HTTPException(404, "ad not found")
    return {"deleted": ad_id}


# ---------------------------------------------------------------------------
# forced-join channels
# ---------------------------------------------------------------------------
class ChannelIn(BaseModel):
    chat_id: str
    title: str | None = None


@router.get("/channels")
async def channels_list(session: DbSession, _admin: int = AdminId):
    rows = await ChannelService(session).list_all()
    return {
        "items": [
            {"id": c.id, "chat_id": c.chat_id, "title": c.title, "is_active": c.is_active}
            for c in rows[:MAX_LIMIT]
        ]
    }


@router.post("/channels", status_code=201)
async def channels_create(body: ChannelIn, session: DbSession, _admin: int = AdminId):
    if not body.chat_id.strip():
        raise HTTPException(400, "chat_id required")
    channel = await ChannelService(session).add(body.chat_id.strip(), title=body.title)
    return {"id": channel.id, "chat_id": channel.chat_id}


@router.delete("/channels/{channel_id}")
async def channels_delete(channel_id: int, session: DbSession, _admin: int = AdminId):
    if not await ChannelService(session).remove(channel_id):
        raise HTTPException(404, "channel not found")
    return {"deleted": channel_id}


# ---------------------------------------------------------------------------
# payments (read + approve CARD only)
# ---------------------------------------------------------------------------
@router.get("/payments")
async def payments_list(
    session: DbSession,
    _admin: int = AdminId,
    status_f: str = Query("", alias="status"),
    limit: int = 20,
    offset: int = 0,
):
    limit, offset = _page(limit, offset)
    stmt = select(Payment)
    if status_f:
        stmt = stmt.where(Payment.status == status_f)
    total = int(await session.scalar(select(func.count()).select_from(stmt.subquery())))
    rows = await session.scalars(
        stmt.order_by(Payment.id.desc()).limit(limit).offset(offset)
    )
    return PageOut(
        total=total, limit=limit, offset=offset,
        items=[
            {
                "id": p.id, "user_id": p.user_id, "amount": p.amount,
                "method": p.method, "status": p.status, "intent": p.intent,
                "created_at": p.created_at,
            }
            for p in rows
        ],
    )


@router.post("/payments/{payment_id}/approve")
async def payments_approve(payment_id: int, session: DbSession, admin_id: int = AdminId):
    payment = await PaymentService(session).get(payment_id)
    if payment is None:
        raise HTTPException(404, "payment not found")
    if payment.method != "card":
        raise HTTPException(400, "only card payments can be approved manually")
    result, payment = await PaymentService(session).approve(payment_id, admin_id)
    return {"result": result, "status": payment.status if payment else None}


# ---------------------------------------------------------------------------
# backups
# ---------------------------------------------------------------------------
@router.get("/backups")
async def backups_list(session: DbSession, _admin: int = AdminId, limit: int = 30, offset: int = 0):
    limit, offset = _page(limit, offset)
    rows = await BackupService(session).list_jobs(limit=MAX_LIMIT)
    items = [
        {
            "id": j.id, "type": j.type, "status": j.status,
            "file_size": j.file_size, "created_at": j.created_at,
        }
        for j in rows[offset:offset + limit]
    ]
    return PageOut(total=len(rows), limit=limit, offset=offset, items=items)


@router.post("/backups", status_code=201)
async def backups_trigger(session: DbSession, admin_id: int = AdminId):
    job = await BackupService(session).create_job(type_="manual")
    return {"id": job.id, "status": job.status}


@router.delete("/backups/{job_id}")
async def backups_delete(job_id: int, session: DbSession, _admin: int = AdminId):
    if not await BackupService(session).delete_job(job_id):
        raise HTTPException(404, "backup not found")
    return {"deleted": job_id}


# ---------------------------------------------------------------------------
# broadcasts
# ---------------------------------------------------------------------------
class BroadcastIn(BaseModel):
    text: str


@router.get("/broadcasts")
async def broadcasts_list(session: DbSession, _admin: int = AdminId, limit: int = 20, offset: int = 0):
    limit, offset = _page(limit, offset)
    total = int(await session.scalar(select(func.count(BroadcastJob.id))) or 0)
    rows = await session.scalars(
        select(BroadcastJob).order_by(BroadcastJob.id.desc()).limit(limit).offset(offset)
    )
    return PageOut(
        total=total, limit=limit, offset=offset,
        items=[
            {
                "id": b.id, "status": b.status, "total": b.total,
                "sent": b.sent, "failed": b.failed, "blocked": b.blocked,
            }
            for b in rows
        ],
    )


@router.post("/broadcasts", status_code=201)
async def broadcasts_create(body: BroadcastIn, session: DbSession, _admin: int = AdminId):
    if not body.text.strip():
        raise HTTPException(400, "text required")
    job = await broadcast_service.create_job(session, text=body.text.strip())
    return {"id": job.id, "total": job.total, "status": job.status}
