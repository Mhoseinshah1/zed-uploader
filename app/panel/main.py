"""Panel router aggregation, static mount, security headers, auth handler."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.panel.deps import PanelAuthRequired
from app.panel.routes import (
    admins,
    ads,
    auth,
    backups,
    bot_plans,
    broadcast,
    commands,
    dashboard,
    folders,
    superadmin,
    license as license_routes,
    media,
    payments,
    plans,
    providers,
    reports,
    review,
    settings as settings_routes,
    stats,
    texts,
    users,
)

STATIC_DIR = Path(__file__).parent / "static"

router = APIRouter(prefix=settings.panel_path, tags=["panel"])
for _module in (
    auth,
    users,
    media,
    review,
    ads,
    folders,
    payments,
    plans,
    providers,
    admins,
    settings_routes,
    stats,
    backups,
    reports,
    texts,
    commands,
    bot_plans,
    superadmin,
    license_routes,
    broadcast,
):
    router.include_router(_module.router)

# Dashboard lives at the panel root ("/panel"); registered directly because an
# empty path can't be attached via include_router.
router.add_api_route("", dashboard.dashboard, methods=["GET"])

_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


class PanelSecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(settings.panel_path):
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["Content-Security-Policy"] = _CSP
        return response


def setup_panel(app: FastAPI) -> None:
    """Mount static, include the router, add headers + auth exception handler."""
    app.mount(
        f"{settings.panel_path}/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="panel-static",
    )
    app.include_router(router)
    app.add_middleware(PanelSecurityHeaders)

    @app.exception_handler(PanelAuthRequired)
    async def _auth_required(request: Request, exc: PanelAuthRequired):
        if exc.want_json:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return RedirectResponse(url=f"{settings.panel_path}/login", status_code=302)
