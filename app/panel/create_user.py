"""CLI to upsert a panel login user.

    python -m app.panel.create_user --username U --password P
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.tenant_context import all_tenants
from app.db.session import async_session_maker
from app.models.panel import PanelUser
from app.panel.security import hash_password


async def upsert(
    username: str, password: str, tenant_id: int = 1, superadmin: bool = False
) -> None:
    # panel_users is a global (platform) table; the DB guard still requires a
    # context, so run this maintenance script in the cross-tenant/global mode.
    with all_tenants():
        async with async_session_maker() as session:
            user = await session.scalar(
                select(PanelUser).where(PanelUser.username == username)
            )
            if user is None:
                session.add(
                    PanelUser(
                        username=username,
                        password_hash=hash_password(password),
                        tenant_id=tenant_id,
                        is_superadmin=superadmin,
                        is_active=True,
                    )
                )
            else:
                user.password_hash = hash_password(password)
                user.tenant_id = tenant_id
                user.is_superadmin = superadmin
                user.is_active = True
            await session.commit()
    role = "superadmin" if superadmin else f"tenant {tenant_id}"
    print(f"panel user '{username}' is ready ({role}).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create/update a panel user")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--tenant-id", type=int, default=1,
        help="tenant this login manages (default: 1 = platform)",
    )
    parser.add_argument(
        "--superadmin", action="store_true",
        help="grant the cross-tenant platform super-admin surface",
    )
    args = parser.parse_args()
    asyncio.run(upsert(args.username, args.password, args.tenant_id, args.superadmin))


if __name__ == "__main__":
    main()
