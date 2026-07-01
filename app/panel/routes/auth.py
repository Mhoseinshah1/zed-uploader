"""Login / logout with rate limiting and double-submit CSRF on the login form."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis_client import get_redis
from app.db.session import get_session
from app.models.panel import PanelUser
from app.panel import security, texts
from app.panel.deps import audit, client_ip, is_secure, render
from app.panel.session import COOKIE_NAME, SESSION_TTL, SessionStore
from fastapi import Depends

router = APIRouter()

_PRECSRF_COOKIE = "zpcsrf"


def _login_url() -> str:
    return f"{settings.panel_path}/login"


@router.get("/login")
async def login_form(request: Request):
    token = security.generate_csrf()
    response = render(request, "login.html", login_csrf=token, error=None)
    response.set_cookie(
        _PRECSRF_COOKIE,
        security.sign(token),
        httponly=True,
        samesite="lax",
        secure=is_secure(request),
        max_age=600,
    )
    return response


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    # double-submit CSRF (no session yet)
    cookie_token = security.unsign(request.cookies.get(_PRECSRF_COOKIE))
    if not security.verify_csrf(csrf_token, cookie_token):
        return render(request, "login.html", login_csrf="", error=texts.CSRF_INVALID)

    redis = get_redis()
    ip = client_ip(request)
    if await security.login_locked(redis, ip, username):
        return render(
            request, "login.html", login_csrf=csrf_token, error=texts.LOGIN_LOCKED
        )

    user = await session.scalar(
        select(PanelUser).where(
            PanelUser.username == username, PanelUser.is_active.is_(True)
        )
    )
    if user is None or not security.verify_password(password, user.password_hash):
        await security.record_login_failure(redis, ip, username)
        return render(
            request, "login.html", login_csrf=csrf_token, error=texts.LOGIN_FAILED
        )

    await security.clear_login_failures(redis, ip, username)
    csrf = security.generate_csrf()
    sid = await SessionStore(redis).create({"uid": user.id, "csrf": csrf})
    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()
    request.state.panel_user = user
    await audit(session, request, "login", target=user.username)

    response = RedirectResponse(url=settings.panel_path, status_code=302)
    response.set_cookie(
        COOKIE_NAME,
        security.sign(sid),
        httponly=True,
        samesite="lax",
        secure=is_secure(request),
        max_age=SESSION_TTL,
    )
    response.delete_cookie(_PRECSRF_COOKIE)
    return response


@router.post("/logout")
@router.get("/logout")
async def logout(request: Request):
    sid = security.unsign(request.cookies.get(COOKIE_NAME))
    if sid:
        await SessionStore(get_redis()).delete(sid)
    response = RedirectResponse(url=_login_url(), status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response
