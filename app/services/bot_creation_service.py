"""BotCreationService — buy-a-bot factory flow (Phase F3).

Charge → tenant-creation is ATOMIC and IDEMPOTENT, exactly like A1's plan
purchase: the wallet debit and the Tenant insert commit together (a crash never
charges without a created tenant), a per-(owner, bot_id) Redis lock folds a
double-tap into one charge, and the ``tenants.bot_id`` UNIQUE constraint is the
DB-level idempotency key (a concurrent duplicate rolls back to one tenant).

Post-commit (best-effort, retryable): seed the new tenant's defaults (its owner
as an admin + a free starter plan) and register the bot in F2's registry (which
sets its webhook). The bot token is encrypted at rest and never logged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.crypto import encrypt_secret
from app.core.logging import get_logger
from app.core.tenant_context import all_tenants, tenant_scope
from app.models.admin import Admin
from app.models.plan import Plan
from app.models.tenant import Tenant
from app.services.bot_plan_service import BotPlanService
from app.services.tenant_service import TenantService
from app.services.wallet_service import InsufficientFunds, WalletService

log = get_logger("bot_creation")

CREATE_LOCK_TTL = 15  # seconds — double-tap debounce per (owner, bot_id)


class BotCreationStatus(str, Enum):
    OK = "ok"
    NOT_AVAILABLE = "not_available"  # bot plan missing/inactive
    ALREADY_REGISTERED = "already_registered"  # bot_id already a tenant
    INSUFFICIENT = "insufficient"
    DUPLICATE = "duplicate"  # concurrent create for the same bot
    FAILED = "failed"  # rolled back — nothing charged, no tenant


@dataclass
class BotCreationResult:
    status: BotCreationStatus
    tenant_id: int | None = None
    bot_username: str | None = None
    expires_at: datetime | None = None
    price: int = 0
    panel_username: str | None = None
    panel_password: str | None = None  # shown once to the buyer, never stored plain


async def validate_bot_token(token: str) -> tuple[int, str | None]:
    """getMe the submitted token -> (bot_id, username). Raises on an invalid
    token. The caller must never echo the token back to the user."""
    bot = Bot(token=token)
    try:
        me = await bot.get_me()
        return me.id, me.username
    finally:
        await bot.session.close()


class BotCreationService:
    def __init__(
        self,
        session: AsyncSession,
        session_maker: async_sessionmaker,
        registry=None,
    ) -> None:
        self.session = session
        self.session_maker = session_maker
        self.registry = registry

    async def create_from_wallet(
        self,
        *,
        owner_user_id: int,
        owner_telegram_id: int,
        plan_key: str,
        bot_id: int,
        bot_username: str | None,
        bot_token: str,
    ) -> BotCreationResult:
        plan = await BotPlanService(self.session).get(plan_key)
        if plan is None or not plan.is_active:
            return BotCreationResult(BotCreationStatus.NOT_AVAILABLE)
        price = plan.price
        duration_days = plan.duration_days

        # already registered? (bot_id is globally unique) — reject before charging
        with all_tenants():
            if await TenantService(self.session).get_by_bot_id(bot_id) is not None:
                return BotCreationResult(BotCreationStatus.ALREADY_REGISTERED)

        from app.core.redis_client import get_redis

        redis = get_redis()
        lock_key = f"botcreate:lock:{owner_user_id}:{bot_id}"
        try:
            acquired = await redis.set(lock_key, "1", nx=True, ex=CREATE_LOCK_TTL)
        except Exception:
            acquired = True  # fail-open: Redis down must not block creation
        if not acquired:
            return BotCreationResult(BotCreationStatus.DUPLICATE)

        release_lock = True
        try:
            if price > 0:
                try:
                    await WalletService(self.session).debit_nocommit(
                        owner_user_id, price, ttype="bot_purchase",
                        reference=f"bot:{bot_id}", description="خرید ربات",
                    )
                except InsufficientFunds:
                    await self.session.rollback()
                    return BotCreationResult(
                        BotCreationStatus.INSUFFICIENT, price=price
                    )

            now = datetime.now(timezone.utc)
            expires = now + timedelta(days=duration_days) if duration_days > 0 else None
            tenant = Tenant(
                owner_user_id=owner_user_id,
                bot_id=bot_id,
                bot_username=bot_username,
                bot_token=encrypt_secret(bot_token),
                plan=plan_key,
                expires_at=expires,
                status="active",
            )
            self.session.add(tenant)
            await self.session.commit()  # debit + tenant, atomically
            tenant_id = tenant.id
            release_lock = False  # success: hold the lock as a debounce window
            log.info("bot_created", tenant_id=tenant_id, owner_user_id=owner_user_id)
        except IntegrityError:  # concurrent create hit the bot_id unique index
            await self.session.rollback()
            return BotCreationResult(BotCreationStatus.DUPLICATE)
        except Exception as exc:  # anything after debit fails -> full rollback
            await self.session.rollback()
            log.error("bot_create_failed", owner_user_id=owner_user_id, error=str(exc))
            return BotCreationResult(BotCreationStatus.FAILED, price=price)
        finally:
            if release_lock:
                try:
                    await redis.delete(lock_key)
                except Exception:
                    pass

        await self._seed_defaults(tenant_id, owner_telegram_id)
        panel_username, panel_password = await self._provision_panel_login(
            tenant_id, bot_id
        )
        await self._register(tenant_id)
        return BotCreationResult(
            BotCreationStatus.OK, tenant_id=tenant_id, bot_username=bot_username,
            expires_at=expires, price=price,
            panel_username=panel_username, panel_password=panel_password,
        )

    async def renew_from_wallet(
        self, *, tenant_id: int, owner_user_id: int, plan_key: str
    ) -> BotCreationResult:
        """Extend a rental (charge + push expires_at forward + reactivate)."""
        plan = await BotPlanService(self.session).get(plan_key)
        if plan is None or not plan.is_active:
            return BotCreationResult(BotCreationStatus.NOT_AVAILABLE)
        price = plan.price
        duration_days = plan.duration_days
        with all_tenants():
            tenant = await TenantService(self.session).get(tenant_id)
        if tenant is None:
            return BotCreationResult(BotCreationStatus.FAILED)

        try:
            if price > 0:
                try:
                    await WalletService(self.session).debit_nocommit(
                        owner_user_id, price, ttype="bot_renewal",
                        reference=f"bot:{tenant.bot_id}", description="تمدید ربات",
                    )
                except InsufficientFunds:
                    await self.session.rollback()
                    return BotCreationResult(
                        BotCreationStatus.INSUFFICIENT, price=price
                    )
            now = datetime.now(timezone.utc)
            base = tenant.expires_at if (tenant.expires_at and tenant.expires_at > now) else now
            expires = base + timedelta(days=duration_days) if duration_days > 0 else None
            tenant.expires_at = expires
            tenant.status = "active"
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            log.error("bot_renew_failed", tenant_id=tenant_id, error=str(exc))
            return BotCreationResult(BotCreationStatus.FAILED, price=price)

        await self._register(tenant_id)
        return BotCreationResult(
            BotCreationStatus.OK, tenant_id=tenant_id, expires_at=expires, price=price
        )

    async def _seed_defaults(self, tenant_id: int, owner_telegram_id: int) -> None:
        """Best-effort per-tenant defaults: the owner as an admin + a free plan."""
        try:
            with tenant_scope(tenant_id):
                async with self.session_maker() as s:
                    s.add(Admin(telegram_id=owner_telegram_id, role="owner", is_active=True))
                    s.add(
                        Plan(
                            key="free", title="رایگان", price=0, duration_days=0,
                            max_files=None, is_active=True,
                        )
                    )
                    await s.commit()
        except Exception as exc:
            log.warning("bot_seed_defaults_failed", tenant_id=tenant_id, error=str(exc))

    async def _provision_panel_login(
        self, tenant_id: int, bot_id: int
    ) -> tuple[str | None, str | None]:
        """Create the customer's tenant-scoped panel login (F4). Returns
        (username, one-time password) to show the buyer; only the bcrypt hash is
        stored. Best-effort — a failure never rolls back the created bot."""
        import secrets

        from app.models.panel import PanelUser
        from app.panel.security import hash_password

        username = f"bot{bot_id}"
        password = secrets.token_urlsafe(9)
        try:
            with all_tenants():  # panel_users is global; set tenant_id explicitly
                async with self.session_maker() as s:
                    existing = await s.scalar(
                        select(PanelUser).where(PanelUser.username == username)
                    )
                    if existing is not None:
                        return username, None  # login already provisioned
                    s.add(
                        PanelUser(
                            username=username, password_hash=hash_password(password),
                            tenant_id=tenant_id, is_active=True,
                        )
                    )
                    await s.commit()
            return username, password
        except Exception as exc:
            log.warning("bot_panel_login_failed", tenant_id=tenant_id, error=str(exc))
            return None, None

    async def _register(self, tenant_id: int) -> None:
        if self.registry is None:
            return
        try:
            await self.registry.reload(tenant_id)
        except Exception as exc:
            log.warning("bot_register_failed", tenant_id=tenant_id, error=str(exc))
