"""Final QA — whole-system end-to-end audit on REAL Postgres (+ shared redis).

Cross-cutting tests that exercise the pieces TOGETHER: all five payment methods
in one mixed batch with the ledger invariant, idempotency under real concurrency
(separate sessions), cross-tenant isolation incl. HTTP callbacks, role/platform
gating, user blocking, license degradation, panel/API security, and multi-bot
webhook routing. Feature-level behavior is covered by the per-phase suites;
this file proves the composition.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import get_redis
from app.core.tenant_context import (
    NoTenantContext,
    all_tenants,
    reset_tenant,
    set_tenant,
    tenant_scope,
)
from app.db.session import get_session
from app.models import (
    Invoice,
    Media,
    PanelUser,
    Payment,
    Plan,
    Subscription,
    SupportTicket,
    Tenant,
    User,
    WalletTransaction,
)
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore
from app.services.gateway_service import GatewayService
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from app.services.providers.base import PaymentProvider, VerifyResult
from app.services.stars_service import ACTIVATED, ALREADY, StarsService
from app.services.subscription_service import PurchaseStatus, SubscriptionService
from app.services.support_service import SupportService
from app.services.wallet_service import WalletService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg

T2, T3, T4 = 2, 3, 4  # customer tenants used across the file


class StubProvider(PaymentProvider):
    """A gateway that always redirects and verifies OK with matching echo."""

    def __init__(self, key: str):
        self.key = key
        self.title = key
        self.verify_calls = 0

    async def create(self, payment: Payment) -> str | None:
        payment.authority = f"AUTH-{self.key}-{payment.id}"
        return f"https://gateway.example/{self.key}/{payment.id}"

    async def verify(self, payment: Payment) -> VerifyResult:
        self.verify_calls += 1
        return VerifyResult(
            ok=True, amount=payment.amount, ref=f"REF-{self.key}-{payment.id}",
            user_id=payment.user_id,
        )


async def _seed_tenants(maker, ids=(T2, T3, T4)) -> None:
    from sqlalchemy import text

    with all_tenants():
        async with maker() as s:
            for tid in ids:
                s.add(
                    Tenant(id=tid, bot_username=f"bot{tid}", bot_id=1000 + tid,
                           status="active")
                )
            await s.commit()
            # explicit ids don't advance the serial — bump it so later
            # auto-id inserts (e.g. bot creation) don't collide
            await s.execute(text(
                "SELECT setval(pg_get_serial_sequence('tenants','id'),"
                " (SELECT MAX(id) FROM tenants))"
            ))
            await s.commit()


async def _user(maker, tenant: int, tg: int, balance: int = 0, **kw) -> int:
    with tenant_scope(tenant):
        async with maker() as s:
            u = User(telegram_id=tg, **kw)
            s.add(u)
            await s.commit()
            if balance:
                await WalletService(s).credit(u.id, balance, reference="seed")
            return u.id


async def _plan(maker, tenant: int, key="pro", price=30000, stars=50) -> None:
    with tenant_scope(tenant):
        async with maker() as s:
            s.add(Plan(key=key, title=key, price=price, duration_days=30,
                       stars_price=stars, is_active=True))
            await s.commit()


async def _ledger_invariant(maker, tenant: int) -> None:
    """SUM(wallet_transactions.amount) == balance, per user, for the tenant."""
    with tenant_scope(tenant):
        async with maker() as s:
            users = list(await s.scalars(select(User)))
            for u in users:
                ledger = int(
                    await s.scalar(
                        select(func.coalesce(func.sum(WalletTransaction.amount), 0))
                        .where(WalletTransaction.user_id == u.id)
                    ) or 0
                )
                assert ledger == u.balance, f"ledger broken for user {u.id}"


# ═══════════════════════════════════════════════════════════════════════════
# 1. All five payment methods end-to-end, together (one mixed batch)
# ═══════════════════════════════════════════════════════════════════════════
async def test_all_five_methods_end_to_end_mixed_batch(pg_sessionmaker):
    await _seed_tenants(pg_sessionmaker)
    await _plan(pg_sessionmaker, T2)
    users = {}
    for i, m in enumerate(("card", "zarinpal", "zibal", "centralpay", "stars")):
        users[m] = await _user(pg_sessionmaker, T2, 60000 + i)

    with tenant_scope(T2):
        # --- card: initiate (pending) -> owner approve --------------------
        async with pg_sessionmaker() as s:
            pay = await PaymentService(s).create(users["card"], 11000, "card", "rcpt")
            st, _ = await PaymentService(s).approve(pay.id, admin_telegram_id=99)
            assert st == "approved"

        # --- three gateways: start -> return-callback verify ---------------
        for key, amount in (("zarinpal", 12000), ("zibal", 13000), ("centralpay", 14000)):
            provider = StubProvider(key)
            async with pg_sessionmaker() as s:
                u = await s.get(User, users[key])
                started = await GatewayService(s, provider).start(u, amount, "topup")
                assert started is not None
                order_id, url = started
                assert url.startswith("https://gateway.example/")
            async with pg_sessionmaker() as s:
                result = await GatewayService(s, provider).verify_and_apply(order_id)
                assert result == "credited"

        # --- Telegram Stars: successful_payment -> plan activated ----------
        async with pg_sessionmaker() as s:
            u = await s.get(User, users["stars"])
            out = await StarsService(s).apply_successful_payment(
                u, "plan:pro", "CHG-E2E-1", 50, "XTR"
            )
            assert out == ACTIVATED

        # --- every method credited exactly once + exactly one invoice ------
        async with pg_sessionmaker() as s:
            for m, expected in (("card", 11000), ("zarinpal", 12000),
                                ("zibal", 13000), ("centralpay", 14000)):
                assert await WalletService(s).balance(users[m]) == expected
            # stars: credited plan.price then debited by the atomic purchase
            assert await WalletService(s).balance(users["stars"]) == 0
            stars_user = await s.get(User, users["stars"])
            assert stars_user.plan == "pro"  # plan activated
            subs = int(await s.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.user_id == users["stars"],
                    Subscription.is_active.is_(True),
                )
            ))
            assert subs == 1

            invoices = list(await s.scalars(select(Invoice).order_by(Invoice.id)))
            by_user = {}
            for inv in invoices:
                by_user.setdefault(inv.user_id, []).append(inv)
            for m in ("card", "zarinpal", "zibal", "centralpay"):
                assert len(by_user[users[m]]) == 1, m  # one topup invoice each
                assert by_user[users[m]][0].kind == "topup"
            assert len(by_user[users["stars"]]) == 1  # one PLAN invoice (no dupes)
            assert by_user[users["stars"]][0].kind == "plan"
            # per-tenant sequential numbering, no gaps or dupes
            numbers = sorted(i.invoice_no for i in invoices)
            assert numbers == list(range(1, len(invoices) + 1))

    await _ledger_invariant(pg_sessionmaker, T2)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Idempotency under real concurrency (separate sessions, real PG)
# ═══════════════════════════════════════════════════════════════════════════
async def test_concurrent_double_gateway_callback(pg_sessionmaker):
    await _seed_tenants(pg_sessionmaker)
    uid = await _user(pg_sessionmaker, T2, 61001)
    provider = StubProvider("zarinpal")
    with tenant_scope(T2):
        async with pg_sessionmaker() as s:
            u = await s.get(User, uid)
            order_id, _ = await GatewayService(s, provider).start(u, 5000, "topup")

        async def _cb():
            async with pg_sessionmaker() as s:  # a fresh session = a real race
                return await GatewayService(s, StubProvider("zarinpal")).verify_and_apply(order_id)

        results = await asyncio.gather(_cb(), _cb())
        assert sorted(results) == ["already", "credited"]  # exactly one credit
        async with pg_sessionmaker() as s:
            assert await WalletService(s).balance(uid) == 5000
            n_inv = int(await s.scalar(select(func.count(Invoice.id))))
            deposits = int(await s.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.type == "deposit")
            ))
    assert n_inv == 1 and deposits == 1
    await _ledger_invariant(pg_sessionmaker, T2)


async def test_concurrent_double_stars_same_charge(pg_sessionmaker):
    await _seed_tenants(pg_sessionmaker)
    await _plan(pg_sessionmaker, T2)
    uid = await _user(pg_sessionmaker, T2, 61002)
    with tenant_scope(T2):
        async def _pay():
            async with pg_sessionmaker() as s:
                u = await s.get(User, uid)
                return await StarsService(s).apply_successful_payment(
                    u, "plan:pro", "CHG-DUP", 50, "XTR"
                )

        results = await asyncio.gather(_pay(), _pay())
        assert ACTIVATED in results and (ALREADY in results or results.count(ACTIVATED) == 1)
        async with pg_sessionmaker() as s:
            rows = int(await s.scalar(
                select(func.count(Payment.id)).where(Payment.provider_ref == "CHG-DUP")
            ))
            subs = int(await s.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.user_id == uid, Subscription.is_active.is_(True))
            ))
            plan_invoices = int(await s.scalar(
                select(func.count(Invoice.id)).where(Invoice.kind == "plan")
            ))
    assert rows == 1        # one charge row despite the duplicate
    assert subs == 1        # activated once
    assert plan_invoices == 1
    await _ledger_invariant(pg_sessionmaker, T2)


async def test_concurrent_double_tap_purchase(pg_sessionmaker):
    await _seed_tenants(pg_sessionmaker)
    await _plan(pg_sessionmaker, T2)
    uid = await _user(pg_sessionmaker, T2, 61003, balance=100000)
    with tenant_scope(T2):
        async def _buy():
            async with pg_sessionmaker() as s:
                u = await s.get(User, uid)
                return (await SubscriptionService(s).purchase(u, "pro")).status

        results = await asyncio.gather(_buy(), _buy())
        assert sorted(r.value for r in results) == ["duplicate", "ok"]
        async with pg_sessionmaker() as s:
            assert await WalletService(s).balance(uid) == 70000  # charged ONCE
    await _ledger_invariant(pg_sessionmaker, T2)


async def test_concurrent_double_bot_creation(pg_sessionmaker):
    from app.services.bot_creation_service import BotCreationService, BotCreationStatus
    from app.services.bot_plan_service import BotPlanService

    with all_tenants():
        async with pg_sessionmaker() as s:
            await BotPlanService(s).upsert("perp", "ربات", 100, 0)
    uid = await _user(pg_sessionmaker, 1, 61004, balance=500)

    with tenant_scope(1):
        async def _create():
            async with pg_sessionmaker() as s:
                svc = BotCreationService(s, pg_sessionmaker, None)
                return (await svc.create_from_wallet(
                    owner_user_id=uid, owner_telegram_id=61004, plan_key="perp",
                    bot_id=888999, bot_username="dupbot", bot_token="123:TOK",
                )).status

        results = await asyncio.gather(_create(), _create())
        assert BotCreationStatus.OK in results
        assert results.count(BotCreationStatus.OK) == 1  # the other folded
    with all_tenants():
        async with pg_sessionmaker() as s:
            n = int(await s.scalar(
                select(func.count(Tenant.id)).where(Tenant.bot_id == 888999)))
    with tenant_scope(1):
        async with pg_sessionmaker() as s:
            assert await WalletService(s).balance(uid) == 400  # charged once
    assert n == 1
    await _ledger_invariant(pg_sessionmaker, 1)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Cross-tenant isolation under load (+ fail-closed, + HTTP callback)
# ═══════════════════════════════════════════════════════════════════════════
async def test_isolation_battery_across_three_tenants(pg_sessionmaker):
    await _seed_tenants(pg_sessionmaker)
    data = {}
    for tid in (T2, T3, T4):
        uid = await _user(pg_sessionmaker, tid, 70000 + tid)
        with tenant_scope(tid):
            async with pg_sessionmaker() as s:
                s.add(Media(code=f"M{tid}", status="approved"))
                pay = Payment(user_id=uid, amount=1000 * tid, method="card", status="pending")
                s.add(pay)
                await s.commit()
                await InvoiceService(s).record(
                    user_id=uid, kind="topup", amount=tid, method="card",
                    source_ref=f"x{tid}",
                )
                await SupportService(s).open_ticket(uid, f"tick{tid}", "hi", "tenant_admin")
        data[tid] = uid

    # from tenant A (T2): every read is scoped; B/C rows are invisible
    with tenant_scope(T2):
        async with pg_sessionmaker() as s:
            from app.services.media_service import MediaService

            assert (await MediaService(s).get_by_code("M2")) is not None
            assert (await MediaService(s).get_by_code("M3")) is None
            assert (await MediaService(s).get_by_code("M4")) is None
            assert {u.telegram_id for u in await s.scalars(select(User))} == {70002}
            assert {p.amount for p in await s.scalars(select(Payment))} == {2000}
            assert {i.amount for i in await s.scalars(select(Invoice))} == {2}
            assert {t.subject for t in await s.scalars(select(SupportTicket))} == {"tick2"}
            # money isolation: crediting ANOTHER tenant's user fails (row invisible)
            try:
                await WalletService(s).credit(data[T3], 999, reference="attack")
                raised = False
            except ValueError:
                raised = True
                await s.rollback()
            assert raised, "credited a foreign tenant's wallet!"

    # a missing tenant context fails CLOSED (override the autouse default)
    token = set_tenant(None)
    try:
        async with pg_sessionmaker() as s:
            try:
                await s.scalar(select(func.count(Media.id)))
                failed_closed = False
            except NoTenantContext:
                failed_closed = True
        assert failed_closed
    finally:
        reset_tenant(token)


async def test_pay_callback_resolves_right_tenant_on_pg(pg_sessionmaker, monkeypatch):
    """The gateway return (no tenant context) settles under the PAYMENT's tenant."""
    await _seed_tenants(pg_sessionmaker)
    uid = await _user(pg_sessionmaker, T3, 71003)
    with tenant_scope(T3):
        async with pg_sessionmaker() as s:
            pay = Payment(user_id=uid, amount=7000, method="zarinpal",
                          provider="zarinpal", status="pending",
                          authority="AUTH-X3", intent="topup")
            s.add(pay)
            await s.commit()

    from app.core.tenant_context import current_tenant

    seen = {}

    async def _stub_verify(session, pid):
        seen["tenant"], seen["pid"] = current_tenant(), pid
        return "credited"

    monkeypatch.setattr("app.api.routes.pay.verify_order", _stub_verify)
    from app.api.main import app

    async def _override():
        async with pg_sessionmaker() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            resp = await c.get("/pay/zarinpal/return",
                               params={"Authority": "AUTH-X3", "Status": "OK"})
        assert resp.status_code == 200
        assert seen["tenant"] == T3  # settled under the payment's OWN tenant
    finally:
        app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════
# 4. Role + platform isolation (H1 / I2) on real PG
# ═══════════════════════════════════════════════════════════════════════════
@pytest_asyncio.fixture
async def panel_env(pg_sessionmaker):
    await _seed_tenants(pg_sessionmaker)
    ids = {}
    with all_tenants():
        async with pg_sessionmaker() as s:
            for name, tenant, role, is_super in (
                ("root", 1, "owner", True),
                ("cust_owner", T2, "owner", False),
                ("cust_support", T2, "support", False),
                ("cust_finance", T2, "finance", False),
            ):
                pu = PanelUser(username=name, password_hash=hash_password("pw"),
                               tenant_id=tenant, role=role, is_superadmin=is_super)
                s.add(pu)
                await s.flush()
                ids[name] = pu.id
            await s.commit()
    from app.api.main import app

    async def _override():
        async with pg_sessionmaker() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield app, ids
    finally:
        app.dependency_overrides.clear()


async def _client(app, uid):
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    return client, csrf


async def test_platform_and_role_gating_on_pg(panel_env):
    app, ids = panel_env
    platform_only = ("/panel/platform", "/panel/platform/tenants",
                     "/panel/platform/support", "/panel/platform/broadcast",
                     "/panel/bot-plans", "/panel/backups", "/panel/license")

    # a customer OWNER is refused on every platform-only surface…
    client, _ = await _client(app, ids["cust_owner"])
    try:
        for path in platform_only:
            assert (await client.get(path)).status_code == 403, path
        # …but reaches their own owner surfaces
        for path in ("/panel/settings", "/panel/providers", "/panel/plans",
                     "/panel/team", "/panel/features"):
            assert (await client.get(path)).status_code == 200, path
    finally:
        await client.aclose()

    # role matrix spot checks on PG
    client, csrf = await _client(app, ids["cust_support"])
    try:
        assert (await client.get("/panel/users")).status_code == 200
        assert (await client.get("/panel/settings")).status_code == 403
        assert (await client.get("/panel/payments")).status_code == 403
    finally:
        await client.aclose()
    client, _ = await _client(app, ids["cust_finance"])
    try:
        assert (await client.get("/panel/payments")).status_code == 200
        assert (await client.get("/panel/providers")).status_code == 403
    finally:
        await client.aclose()

    # the platform super-admin still reaches everything
    client, _ = await _client(app, ids["root"])
    try:
        for path in platform_only:
            assert (await client.get(path)).status_code == 200, path
    finally:
        await client.aclose()


async def test_bot_factory_stays_platform_only(pg_sessionmaker):
    from app.bot.handlers import newbot
    from app.core.tenant_context import is_platform

    with tenant_scope(T2):
        assert is_platform() is False
        msg = SimpleNamespace(text="x", answer=AsyncMock(),
                              from_user=SimpleNamespace(id=1))
        await newbot.newbot_command(msg, AsyncMock(), session=object(), db_user=None)
        from app.bot import messages
        msg.answer.assert_awaited_once_with(messages.NEWBOT_ONLY_PLATFORM)
    with tenant_scope(1):
        assert is_platform() is True


# ═══════════════════════════════════════════════════════════════════════════
# 5. User blocking (I1) on real PG
# ═══════════════════════════════════════════════════════════════════════════
async def test_blocked_user_full_battery_on_pg(pg_sessionmaker):
    from aiogram.types import User as TgUser

    from app.bot.delivery import DeliveryStatus, deliver_by_code
    from app.bot.middlewares.blocked import BlockedUserMiddleware
    from app.models import Admin
    from app.services import broadcast as bcast

    await _seed_tenants(pg_sessionmaker)
    with tenant_scope(T2):
        async with pg_sessionmaker() as s:
            s.add_all([
                User(telegram_id=81001, is_blocked=True),
                User(telegram_id=81002, is_blocked=False),
                User(telegram_id=81003, is_blocked=True),  # blocked BUT an admin
            ])
            s.add(Admin(telegram_id=81003, role="owner", is_active=True))
            s.add(Media(code="BLK", status="approved"))
            await s.commit()

        # deep-link delivery refused for blocked, allowed for the blocked admin
        async with pg_sessionmaker() as s:
            res = await deliver_by_code(
                object(), s, chat_id=81001,
                user=TgUser(id=81001, is_bot=False, first_name="x"), code="BLK",
            )
            assert res.status is DeliveryStatus.BLOCKED
        async with pg_sessionmaker() as s:
            res = await deliver_by_code(
                AsyncMock(), s, chat_id=81003,
                user=TgUser(id=81003, is_bot=False, first_name="x"), code="BLK",
            )
            assert res.status is not DeliveryStatus.BLOCKED  # admin bypass

        # middleware stops a blocked user's wallet/purchase updates (real session)
        async with pg_sessionmaker() as s:
            blocked = await s.scalar(select(User).where(User.telegram_id == 81001))
            handled = {"n": 0}

            async def _handler(event, data):
                handled["n"] += 1

            msg = SimpleNamespace(answer=AsyncMock())
            await BlockedUserMiddleware()(
                _handler, SimpleNamespace(message=msg),
                {"db_user": blocked, "session": s},
            )
            assert handled["n"] == 0  # never reached the wallet handler
            msg.answer.assert_awaited_once()

        # broadcast snapshot: EVERY is_blocked row is excluded — including the
        # blocked admin (the admin bypass is for interactive use, not broadcasts)
        async with pg_sessionmaker() as s:
            job = await bcast.create_job(s, text="hi")
            assert job.total == 1  # only 81002 remains


# ═══════════════════════════════════════════════════════════════════════════
# 6. License degradation (E)
# ═══════════════════════════════════════════════════════════════════════════
async def test_license_degrades_paid_actions_only(pg_sessionmaker, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from aiogram.types import User as TgUser

    from app.bot.delivery import DeliveryStatus, deliver_by_code
    from app.core.config import settings
    from app.models.license import LicenseInfo
    from app.services.license_service import evaluate, paid_features_allowed

    await _seed_tenants(pg_sessionmaker)
    now = datetime.now(timezone.utc)

    # bypassed (LICENSE_DISABLED default) -> everything allowed
    with tenant_scope(T2):
        async with pg_sessionmaker() as s:
            assert await paid_features_allowed(s) is True

    # simulate a REAL license config with an expired row
    monkeypatch.setattr(settings, "license_disabled", False)
    monkeypatch.setattr(settings, "license_key", "KEY-QA")
    monkeypatch.setattr(settings, "license_server_url", "http://act")
    with all_tenants():
        async with pg_sessionmaker() as s:
            row = LicenseInfo(status="active", expires_at=now - timedelta(days=1),
                              last_ok_at=now)
            s.add(row)
            await s.commit()
    with tenant_scope(T2):
        async with pg_sessionmaker() as s:
            assert await paid_features_allowed(s) is False  # expired -> degrade

            # …but DELIVERY and data access stay intact
            from app.models import MediaFile

            s.add(User(telegram_id=82001))
            media = Media(code="LIC", status="approved")
            s.add(media)
            await s.flush()
            s.add(MediaFile(media_id=media.id, file_type="document",
                            telegram_file_id="F1"))
            await s.commit()
        async with pg_sessionmaker() as s:
            res = await deliver_by_code(
                AsyncMock(), s, chat_id=82001,
                user=TgUser(id=82001, is_bot=False, first_name="x"), code="LIC",
            )
            assert res.status is DeliveryStatus.DELIVERED  # license never gates it

    # offline grace: an active row inside/outside the grace window
    grace = settings.license_grace_days
    ok_row = LicenseInfo(status="active", expires_at=None,
                         last_ok_at=now - timedelta(days=max(0, grace - 1)))
    stale_row = LicenseInfo(status="active", expires_at=None,
                            last_ok_at=now - timedelta(days=grace + 1))
    assert evaluate(ok_row, now) is True      # inside grace -> honored
    assert evaluate(stale_row, now) is False  # beyond grace -> degrade
    assert evaluate(SimpleNamespace(status="revoked", expires_at=None,
                                    last_ok_at=now), now) is False


# ═══════════════════════════════════════════════════════════════════════════
# 7. Panel + API security on real PG
# ═══════════════════════════════════════════════════════════════════════════
async def test_csrf_required_and_api_v1_tenant_bound(panel_env, pg_sessionmaker):
    app, ids = panel_env
    uid_a = await _user(pg_sessionmaker, T2, 83001)
    with tenant_scope(T2):
        async with pg_sessionmaker() as s:
            s.add(Media(code="MINE", status="approved"))
            await s.commit()
    with tenant_scope(T3):
        async with pg_sessionmaker() as s:
            s.add(Media(code="THEIRS", status="approved"))
            await s.commit()

    # CSRF: a mutating panel POST without the token is refused (403), with it 302
    client, csrf = await _client(app, ids["cust_owner"])
    try:
        no_token = await client.post(f"/panel/users/{uid_a}/block", data={},
                                     follow_redirects=False)
        assert no_token.status_code == 403
        with_token = await client.post(
            f"/panel/users/{uid_a}/block", data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert with_token.status_code == 302
    finally:
        await client.aclose()

    # /api/v1 binds the caller's tenant and never leaks secrets
    from app.core import jwt_utils

    token = jwt_utils.encode(ids["cust_owner"])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        resp = await c.get("/api/v1/media",
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        codes = [m["code"] for m in resp.json()["items"]]
        assert codes == ["MINE"]           # never the other tenant's media
        assert "password_hash" not in resp.text
        users = await c.get("/api/v1/users",
                            headers={"Authorization": f"Bearer {token}"})
        assert "password_hash" not in users.text


async def test_panel_never_renders_decrypted_bot_token(panel_env, pg_sessionmaker):
    from app.services.tenant_service import TenantService

    PLAINTEXT = "999888:SUPER_SECRET_TOKEN"
    app, ids = panel_env
    with all_tenants():
        async with pg_sessionmaker() as s:
            await TenantService(s).create(
                owner_user_id=None, bot_id=424242, bot_username="sec",
                bot_token=PLAINTEXT,
            )
    client, _ = await _client(app, ids["root"])
    try:
        for path in ("/panel/platform/tenants", "/panel/platform"):
            resp = await client.get(path)
            assert resp.status_code == 200
            assert PLAINTEXT not in resp.text  # decrypted token NEVER rendered
    finally:
        await client.aclose()


# ═══════════════════════════════════════════════════════════════════════════
# 8. Multi-bot webhook routing
# ═══════════════════════════════════════════════════════════════════════════
async def test_tenant_webhook_routing_secret_and_suspension(pg_sessionmaker):
    from app.bot.registry import BotRegistry, RegisteredBot
    from app.api.main import app

    class _CapturingDp:
        def __init__(self):
            self.calls = []

        async def feed_update(self, bot, update, **kw):
            self.calls.append((bot, kw))

    fake_bot = SimpleNamespace(token="x")
    registry = BotRegistry(pg_sessionmaker)
    registry._bots[555] = RegisteredBot(tenant_id=T2, bot_id=555, bot=fake_bot,
                                        secret="S3CRET")
    dp = _CapturingDp()
    old_reg = getattr(app.state, "registry", None)
    old_dp = getattr(app.state, "dp", None)
    app.state.registry, app.state.dp = registry, dp

    update = {"update_id": 1}
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            # unknown bot -> 404 (no dispatch)
            assert (await c.post("/tenant/999/webhook", json=update)).status_code == 404
            # wrong secret -> 403 (no dispatch)
            wrong = await c.post(
                "/tenant/555/webhook", json=update,
                headers={"X-Telegram-Bot-Api-Secret-Token": "WRONG"},
            )
            assert wrong.status_code == 403 and dp.calls == []
            # right secret -> dispatched with THIS tenant's id and THIS bot
            ok = await c.post(
                "/tenant/555/webhook", json=update,
                headers={"X-Telegram-Bot-Api-Secret-Token": "S3CRET"},
            )
            assert ok.status_code == 200
            assert len(dp.calls) == 1
            bot, kw = dp.calls[0]
            assert bot is fake_bot and kw["tenant_id"] == T2
            # suspension: unregistering from the registry stops serving (404)
            await registry.unregister(555)
            gone = await c.post(
                "/tenant/555/webhook", json=update,
                headers={"X-Telegram-Bot-Api-Secret-Token": "S3CRET"},
            )
            assert gone.status_code == 404 and len(dp.calls) == 1
    finally:
        app.state.registry, app.state.dp = old_reg, old_dp
