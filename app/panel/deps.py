"""Panel dependencies: auth, CSRF, audit, templating."""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.db.session import get_session
from app.models.panel import PanelAudit, PanelUser
from app.panel import security
from app.panel.session import COOKIE_NAME, SessionStore

log = get_logger("panel")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class PanelAuthRequired(Exception):
    """Raised when a panel route needs auth. Handled app-side (302 / 401)."""

    def __init__(self, want_json: bool) -> None:
        self.want_json = want_json


def is_secure(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _wants_json(request: Request) -> bool:
    return (
        request.headers.get("hx-request") == "true"
        or "application/json" in request.headers.get("accept", "")
    )


async def require_panel_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> PanelUser:
    sid = security.unsign(request.cookies.get(COOKIE_NAME))
    data = await SessionStore(get_redis()).get(sid) if sid else None
    user = None
    if data and data.get("uid"):
        user = await session.scalar(
            select(PanelUser).where(
                PanelUser.id == data["uid"], PanelUser.is_active.is_(True)
            )
        )
    if not data or user is None:
        raise PanelAuthRequired(_wants_json(request))
    request.state.panel_session = data
    request.state.panel_user = user
    return user


async def verify_csrf(request: Request) -> None:
    data = getattr(request.state, "panel_session", None)
    session_token = data.get("csrf") if data else None
    token = request.headers.get("x-csrf-token")
    if token is None:
        form = await request.form()
        token = form.get("csrf_token")
    if not security.verify_csrf(token, session_token):
        raise HTTPException(status_code=403, detail="CSRF token invalid")


async def audit(
    session: AsyncSession, request: Request, action: str, target: str | None = None
) -> None:
    user = getattr(request.state, "panel_user", None)
    session.add(
        PanelAudit(
            panel_user_id=user.id if user else None,
            action=action,
            target=target,
            ip=client_ip(request),
        )
    )
    await session.commit()
    log.info(
        "panel_action",
        action=action,
        target=target,
        user=user.username if user else None,
    )


def render(request: Request, template: str, **context):
    data = getattr(request.state, "panel_session", None)
    base = {
        "request": request,
        "panel_path": settings.panel_path,
        "csrf_token": data.get("csrf") if data else "",
        "current_user": getattr(request.state, "panel_user", None),
    }
    base.update(context)
    return templates.TemplateResponse(request, template, base)
