"""Panel dependencies: auth, CSRF, audit, templating."""
from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.core.tenant_context import (
    ALL_TENANTS,
    current_tenant,
    reset_tenant,
    set_tenant,
)
from app.db.session import get_session
from app.models.panel import PanelAudit, PanelUser
from app.panel import security
from app.panel.session import COOKIE_NAME, SessionStore

log = get_logger("panel")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

STATIC_DIR = Path(__file__).parent / "static"


def _asset_version() -> str:
    """Short content hash of the panel's CSS+JS, used as a ``?v=`` cache-buster.

    Computed once at import from the bytes on disk (build-time in the image), so
    the query string changes whenever the stylesheet or script changes and
    browsers/proxies stop serving a stale cached copy. Pure presentation glue —
    no request, DB, or env access.
    """
    h = hashlib.sha1()
    for rel in ("css/panel.css", "js/panel.js"):
        try:
            h.update((STATIC_DIR / rel).read_bytes())
        except OSError:
            continue
    return h.hexdigest()[:10]


ASSET_VERSION = _asset_version()


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
) -> AsyncIterator[PanelUser]:
    """Authenticate the panel session AND bind the request to the login's tenant.

    Generator dependency: after loading the (global) PanelUser under the
    platform bootstrap context set by ``get_session``, it switches the tenant
    context to ``panel_user.tenant_id`` for the rest of the request, so EVERY
    scoped panel query (media, users, payments, …) is filtered to that customer
    (F1 guard). The context is reset on teardown — LIFO before get_session's —
    so cross-tenant data is never reachable and a missing tenant still fails
    closed.
    """
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
    token = set_tenant(user.tenant_id)
    try:
        yield user
    finally:
        reset_tenant(token)


async def require_superadmin(
    request: Request, user: PanelUser = Depends(require_panel_user)
) -> AsyncIterator[PanelUser]:
    """Gate + context for the cross-tenant platform surface (F5).

    A non-super-admin login (any customer) is rejected with 403 — strict role
    separation. For a super-admin, switch to the explicit ALL_TENANTS context so
    the audited platform routes can read across tenants; this is the ONLY place
    that bypasses per-tenant filtering. Reset on teardown (LIFO). Super-admin
    audits carry tenant_id=NULL (a platform action).
    """
    if not user.is_superadmin:
        raise HTTPException(status_code=403, detail="forbidden")
    token = set_tenant(ALL_TENANTS)
    try:
        yield user
    finally:
        reset_tenant(token)


def has_role(user: PanelUser | None, *roles: str) -> bool:
    """True if the login may use a role-gated control (super-admin always may).

    Registered as a Jinja global so templates can hide controls the current
    panel user isn't allowed to use.
    """
    if user is None:
        return False
    if getattr(user, "is_superadmin", False):
        return True
    return getattr(user, "role", None) in roles


templates.env.globals["has_role"] = has_role


def require_role(*roles: str):
    """Panel dependency factory (I2): require the login's role be in ``roles``.

    Builds on ``require_panel_user`` (so the tenant context is bound + reset the
    same way). The platform super-admin bypasses tenant roles entirely. A login
    whose role is not allowed gets 403.
    """

    async def _dep(user: PanelUser = Depends(require_panel_user)) -> PanelUser:
        if user.is_superadmin or getattr(user, "role", None) in roles:
            return user
        raise HTTPException(status_code=403, detail="forbidden")

    return _dep


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
    ctx = current_tenant()
    session.add(
        PanelAudit(
            panel_user_id=user.id if user else None,
            tenant_id=ctx if isinstance(ctx, int) else None,  # the acting tenant
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
    theme = request.cookies.get("panel_theme")
    base = {
        "request": request,
        "panel_path": settings.panel_path,
        "csrf_token": data.get("csrf") if data else "",
        "current_user": getattr(request.state, "panel_user", None),
        "theme": "light" if theme == "light" else "dark",
        "asset_version": ASSET_VERSION,
    }
    base.update(context)
    return templates.TemplateResponse(request, template, base)
