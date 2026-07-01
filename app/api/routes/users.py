"""Users list endpoint — protected by rate limit + API key."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import ApiKeyDep, DbSession, RateLimitDep
from app.schemas.user import UserOut
from app.services.user_service import UserService

router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[RateLimitDep, ApiKeyDep],
)


@router.get("", response_model=list[UserOut])
async def list_users(
    session: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[UserOut]:
    items = await UserService(session).list_users(limit=limit, offset=offset)
    return [UserOut.model_validate(item) for item in items]
