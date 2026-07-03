"""L1 — refund (exactly once, ledger-consistent, plan policy) + reconcile
(settle-once, expiry) + owner/finance-only routes with audit."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.redis_client import get_redis
from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Base, Invoice, PanelUser, Payment, Tenant, User, WalletTransaction
from app.models.panel import PanelAudit
from app.models.subscription import Subscription
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore
from app.services.payment_service import PaymentService
from app.services.reconcile_service import reconcile_pending
from app.services.refund_service import (
    ALREADY,
    INSUFFICIENT,
    NOT_FOUND,
    NOT_SETTLED,
    REFUNDED,
    RefundService,
)
from app.services.wallet_service import WalletService

T = 2
PANEL = settings.panel_path


@pytest_asyncio.fixture
async def sm():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    with all_tenants():
        async with Session() as s:
            s.add(Tenant(id=T, bot_username="a", bot_id=2002, status="active"))
            s.add(Tenant(id=3, bot_username="b", bot_id=3003, status="active"))
            await s.commit()
    try:
        yield Session
    finally:
        await engine.dispose()


async def _user(sm, tg):
    with tenant_scope(T):
        async with sm() as s:
            u = User(telegram_id=tg)
            s.add(u)
            await s.commit()
            return u.id


async def _approved_topup(sm, uid, amount):
    """A settled card top-up THROUGH the real approve (credits the wallet)."""
    with tenant_scope(T):
        async with sm() as s:
            p = await PaymentService(s).create(uid, amount, "card", receipt="r")
            result, _ = await PaymentService(s).approve(p.id, admin_telegram_id=0)
            assert result == "approved"
            return p.id


async def _ledger_ok(s, uid):
    total = int(
        await s.scalar(
            select(func.coalesce(func.sum(WalletTransaction.amount), 0)).where(
                WalletTransaction.user_id == uid
            )
        )
    )
    assert total == await WalletService(s).balance(uid)  # invariant holds


# --- refund: top-up -----------------------------------------------------------
async def test_topup_refund_reverses_exactly_once(sm):
    uid = await _user(sm, 6001)
    pid = await _approved_topup(sm, uid, 5000)
    with tenant_scope(T):
        async with sm() as s:
            assert await WalletService(s).balance(uid) == 5000
            assert await RefundService(s).refund(
                pid, panel_user_id=42, reason="اشتباه واریز"
            ) == REFUNDED
        async with sm() as s:
            assert await WalletService(s).balance(uid) == 0
            p = await s.get(Payment, pid)
            assert p.status == "refunded" and p.refunded_by == 42
            assert p.refund_reason == "اشتباه واریز" and p.refunded_at is not None
            n_refund_tx = int(await s.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.reference == f"refund:payment:{pid}"
                )
            ))
            assert n_refund_tx == 1
            inv = await s.scalar(select(Invoice).where(Invoice.kind == "refund"))
            assert inv is not None and inv.amount == 5000  # credit note
            await _ledger_ok(s, uid)

        # double submit: no second reversal, ledger untouched
        async with sm() as s:
            assert await RefundService(s).refund(
                pid, panel_user_id=42, reason="دوباره"
            ) == ALREADY
        async with sm() as s:
            assert await WalletService(s).balance(uid) == 0
            n_tx = int(await s.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.reference == f"refund:payment:{pid}"
                )
            ))
            assert n_tx == 1
            await _ledger_ok(s, uid)


async def test_refund_refused_when_wallet_already_spent(sm):
    uid = await _user(sm, 6002)
    pid = await _approved_topup(sm, uid, 5000)
    with tenant_scope(T):
        async with sm() as s:
            await WalletService(s).debit(uid, 4000, reference="spent")
        async with sm() as s:
            assert await RefundService(s).refund(
                pid, panel_user_id=1, reason="x"
            ) == INSUFFICIENT
        async with sm() as s:
            p = await s.get(Payment, pid)
            assert p.status == "approved"  # untouched — refundable later
            assert await WalletService(s).balance(uid) == 1000  # never negative
            await _ledger_ok(s, uid)


# --- refund: plan intent --------------------------------------------------------
async def test_plan_refund_revokes_plan_moves_no_money(sm):
    uid = await _user(sm, 6003)
    with tenant_scope(T):
        async with sm() as s:
            # the state a settled plan-intent gateway payment leaves behind:
            # net wallet 0, plan active
            p = Payment(user_id=uid, amount=9000, method="zarinpal",
                        provider="zarinpal", status="approved", intent="plan:plus")
            user = await s.get(User, uid)
            user.plan = "plus"
            s.add(p)
            s.add(Subscription(user_id=uid, plan="plus",
                               starts_at=datetime.now(timezone.utc), is_active=True))
            await s.commit()
            pid = p.id
        async with sm() as s:
            assert await RefundService(s).refund(
                pid, panel_user_id=7, reason="انصراف"
            ) == REFUNDED
        async with sm() as s:
            user = await s.get(User, uid)
            assert user.plan == "free" and user.plan_expires_at is None
            sub = await s.scalar(select(Subscription).where(Subscription.user_id == uid))
            assert sub.is_active is False
            assert await WalletService(s).balance(uid) == 0  # NO wallet movement
            n_tx = int(await s.scalar(select(func.count(WalletTransaction.id)).where(
                WalletTransaction.user_id == uid
            )))
            assert n_tx == 0
            assert (await s.get(Payment, pid)).status == "refunded"


async def test_plan_refund_leaves_a_different_current_plan_alone(sm):
    uid = await _user(sm, 6004)
    with tenant_scope(T):
        async with sm() as s:
            p = Payment(user_id=uid, amount=9000, method="zibal",
                        status="approved", intent="plan:plus")
            user = await s.get(User, uid)
            user.plan = "pro"  # user has since bought a DIFFERENT plan
            s.add(p)
            await s.commit()
            pid = p.id
        async with sm() as s:
            assert await RefundService(s).refund(
                pid, panel_user_id=7, reason="x"
            ) == REFUNDED
        async with sm() as s:
            assert (await s.get(User, uid)).plan == "pro"  # untouched per policy
            assert (await s.get(Payment, pid)).status == "refunded"


# --- refund: guards --------------------------------------------------------------
async def test_refund_only_settled_and_tenant_scoped(sm):
    uid = await _user(sm, 6005)
    with tenant_scope(T):
        async with sm() as s:
            p = Payment(user_id=uid, amount=100, method="card", status="pending")
            s.add(p)
            await s.commit()
            pid = p.id
        async with sm() as s:
            assert await RefundService(s).refund(pid, panel_user_id=1, reason="") == NOT_SETTLED
    with tenant_scope(3):  # another tenant cannot even see the payment
        async with sm() as s:
            assert await RefundService(s).refund(pid, panel_user_id=1, reason="") == NOT_FOUND


# --- reconcile --------------------------------------------------------------------
async def test_reconcile_settles_once_expires_stale_keeps_fresh(sm):
    uid = await _user(sm, 6006)
    with tenant_scope(T):
        async with sm() as s:
            paid = Payment(user_id=uid, amount=7000, method="zarinpal",
                           provider="zarinpal", status="pending")
            stale = Payment(user_id=uid, amount=100, method="zibal",
                            provider="zibal", status="pending")
            fresh = Payment(user_id=uid, amount=200, method="zibal",
                            provider="zibal", status="pending")
            card = Payment(user_id=uid, amount=300, method="card", status="pending")
            s.add_all([paid, stale, fresh, card])
            await s.commit()
            ids = {"paid": paid.id, "stale": stale.id, "fresh": fresh.id, "card": card.id}
            stale_row = await s.get(Payment, ids["stale"])
            stale_row.created_at = datetime.now(timezone.utc) - timedelta(days=2)
            await s.commit()

        async def fake_verify(session, order_id):
            """The gateway's answer — settle mimics the real idempotent verify."""
            row = await session.scalar(
                select(Payment).where(Payment.id == order_id).with_for_update()
            )
            if row.status == "approved":
                return "already"
            if row.id != ids["paid"]:
                return "failed"  # gateway says unpaid
            row.status = "approved"
            await WalletService(session).credit(
                row.user_id, row.amount, reference=f"zarinpal:ref{row.id}"
            )
            return "credited"

        async with sm() as s:
            report = await reconcile_pending(s, verify=fake_verify)
            assert report == {"settled": 1, "already": 0, "mismatch": 0,
                              "expired": 1, "pending": 1}
        async with sm() as s:
            assert (await s.get(Payment, ids["paid"])).status == "approved"
            assert (await s.get(Payment, ids["stale"])).status == "expired"
            assert (await s.get(Payment, ids["fresh"])).status == "pending"
            assert (await s.get(Payment, ids["card"])).status == "pending"  # skipped
            assert await WalletService(s).balance(uid) == 7000

        # a second run re-credits NOTHING: the settled row is no longer pending
        async with sm() as s:
            report = await reconcile_pending(s, verify=fake_verify)
            assert report["settled"] == 0 and report["expired"] == 0
        async with sm() as s:
            assert await WalletService(s).balance(uid) == 7000  # exactly once
            await _ledger_ok(s, uid)


# --- panel routes: role gate + audit ----------------------------------------------
async def test_refund_and_reconcile_are_finance_only_and_audited(sm):
    uid = await _user(sm, 6007)
    pid = await _approved_topup(sm, uid, 1000)
    with all_tenants():
        async with sm() as s:
            fin = PanelUser(username="l1_fin", password_hash=hash_password("x" * 8),
                            tenant_id=T, role="finance", is_superadmin=False)
            sup = PanelUser(username="l1_sup", password_hash=hash_password("x" * 8),
                            tenant_id=T, role="support", is_superadmin=False)
            s.add_all([fin, sup])
            await s.commit()
            fin_id, sup_id = fin.id, sup.id

    from app.api.main import app
    from app.db.session import get_session

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override

    async def _client(user_id):
        csrf = security.generate_csrf()
        sid = await SessionStore(get_redis()).create(
            {"uid": user_id, "csrf": csrf, "epoch": 0}
        )
        c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
        c.cookies.set(COOKIE_NAME, security.sign(sid))
        return c, csrf

    try:
        support, scsrf = await _client(sup_id)
        try:
            r = await support.post(f"{PANEL}/payments/{pid}/refund",
                                   data={"reason": "x", "csrf_token": scsrf},
                                   follow_redirects=False)
            assert r.status_code == 403
            r = await support.post(f"{PANEL}/payments/reconcile",
                                   data={"csrf_token": scsrf}, follow_redirects=False)
            assert r.status_code == 403
        finally:
            await support.aclose()

        finance, fcsrf = await _client(fin_id)
        try:
            r = await finance.post(f"{PANEL}/payments/{pid}/refund",
                                   data={"reason": "test", "csrf_token": fcsrf},
                                   follow_redirects=False)
            assert r.status_code == 302 and "msg=refunded" in r.headers["location"]
            r = await finance.post(f"{PANEL}/payments/reconcile",
                                   data={"csrf_token": fcsrf}, follow_redirects=False)
            assert r.status_code == 302 and "reconciled=1" in r.headers["location"]
        finally:
            await finance.aclose()
    finally:
        app.dependency_overrides.clear()

    with tenant_scope(T):
        async with sm() as s:
            assert (await s.get(Payment, pid)).status == "refunded"
            assert await WalletService(s).balance(uid) == 0
    async with sm() as s:  # PanelAudit is a global table
        acts = set((await s.scalars(select(PanelAudit.action))).all())
        assert "payment_refund" in acts and "payments_reconcile" in acts
