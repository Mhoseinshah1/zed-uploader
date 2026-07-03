"""H4 — invoices / receipts.

One invoice per settled payment (idempotent, no dup on double-callback), correct
per-tenant sequential numbering, right amount/method/kind, and tenant isolation
(service + settlement hooks + panel list/CSV).
"""
from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.redis_client import get_redis
from app.core.tenant_context import all_tenants, tenant_scope
from app.db.session import get_session
from app.models import Base, Invoice, PanelUser, Payment, Plan, Tenant, User
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from app.services.subscription_service import PurchaseStatus, SubscriptionService
from app.services.wallet_service import WalletService

T_A = 2
T_B = 3


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
            s.add_all([
                Tenant(id=T_A, bot_username="a", bot_id=2002, status="active"),
                Tenant(id=T_B, bot_username="b", bot_id=3003, status="active"),
            ])
            await s.commit()
    try:
        yield Session
    finally:
        await engine.dispose()


async def _user(Session, tenant, tg, balance=0):
    with tenant_scope(tenant):
        async with Session() as s:
            u = User(telegram_id=tg)
            s.add(u)
            await s.commit()
            uid = u.id
            if balance:
                await WalletService(s).credit(uid, balance, reference="seed")
    return uid


# --- service: idempotency + per-tenant numbering + isolation ---------------
async def test_record_is_idempotent_by_source(sm):
    uid = await _user(sm, T_A, 5001)
    with tenant_scope(T_A):
        async with sm() as s:
            a = await InvoiceService(s).record(
                user_id=uid, kind="topup", amount=100, method="card", source_ref="dup"
            )
        async with sm() as s:
            b = await InvoiceService(s).record(
                user_id=uid, kind="topup", amount=999, method="card", source_ref="dup"
            )
        async with sm() as s:
            n = await s.scalar(select(func.count(Invoice.id)))
    assert a.invoice_no == b.invoice_no and n == 1  # one row, no duplicate
    assert b.amount == 100  # the second call did NOT overwrite


async def test_numbering_is_sequential_per_tenant(sm):
    ua = await _user(sm, T_A, 5001)
    ub = await _user(sm, T_B, 6001)
    with tenant_scope(T_A):
        async with sm() as s:
            i1 = await InvoiceService(s).record(user_id=ua, kind="topup", amount=1, method="card", source_ref="x1")
        async with sm() as s:
            i2 = await InvoiceService(s).record(user_id=ua, kind="plan", amount=2, method="wallet", source_ref="x2")
    with tenant_scope(T_B):
        async with sm() as s:
            j1 = await InvoiceService(s).record(user_id=ub, kind="topup", amount=3, method="card", source_ref="x1")
    assert (i1.invoice_no, i2.invoice_no) == (1, 2)
    assert j1.invoice_no == 1  # per-tenant sequence restarts for tenant B


async def test_invoices_isolated_across_tenants(sm):
    ua = await _user(sm, T_A, 5001)
    ub = await _user(sm, T_B, 6001)
    with tenant_scope(T_A):
        async with sm() as s:
            await InvoiceService(s).record(user_id=ua, kind="topup", amount=1, method="card", source_ref="a")
    with tenant_scope(T_B):
        async with sm() as s:
            await InvoiceService(s).record(user_id=ub, kind="plan", amount=2, method="wallet", source_ref="a")
    with tenant_scope(T_A):
        async with sm() as s:
            rows = await InvoiceService(s).list_for_tenant()
    assert len(rows) == 1 and rows[0].kind == "topup"  # never sees tenant B's


# --- settlement hooks ------------------------------------------------------
async def test_card_topup_invoice_exactly_once(sm):
    with tenant_scope(T_A):
        async with sm() as s:
            u = User(telegram_id=5001)
            s.add(u)
            await s.commit()
            pay = Payment(user_id=u.id, amount=50000, method="card", status="pending")
            s.add(pay)
            await s.commit()
            pid = pay.id
    with tenant_scope(T_A):
        async with sm() as s:
            st, _ = await PaymentService(s).approve(pid, 999)
            assert st == "approved"
        async with sm() as s:  # double-approve (double-callback) -> already, no dup
            st2, _ = await PaymentService(s).approve(pid, 999)
            assert st2 == "already"
        async with sm() as s:
            rows = await InvoiceService(s).list_for_tenant()
    assert len(rows) == 1
    inv = rows[0]
    assert inv.kind == "topup" and inv.amount == 50000 and inv.method == "card"
    assert inv.source_ref == f"payment:{pid}" and inv.invoice_no == 1


async def test_plan_purchase_invoice_records_method(sm):
    uid = await _user(sm, T_A, 5001, balance=100000)
    with tenant_scope(T_A):
        async with sm() as s:
            s.add(Plan(key="pro", title="Pro", price=30000, duration_days=30, is_active=True))
            await s.commit()
        async with sm() as s:
            user = await s.get(User, uid)
            res = await SubscriptionService(s).purchase(user, "pro", method="stars")
            assert res.status is PurchaseStatus.OK and res.invoice_no is not None
        async with sm() as s:
            rows = [i for i in await InvoiceService(s).list_for_tenant() if i.kind == "plan"]
    assert len(rows) == 1
    assert rows[0].method == "stars" and rows[0].amount == 30000
    assert rows[0].source_ref.startswith("sub:")


async def test_free_plan_makes_no_invoice(sm):
    uid = await _user(sm, T_A, 5001)
    with tenant_scope(T_A):
        async with sm() as s:
            s.add(Plan(key="free", title="Free", price=0, duration_days=0, is_active=True))
            await s.commit()
        async with sm() as s:
            user = await s.get(User, uid)
            res = await SubscriptionService(s).purchase(user, "free")
            assert res.status is PurchaseStatus.OK and res.invoice_no is None
        async with sm() as s:
            n = await s.scalar(select(func.count(Invoice.id)))
    assert n == 0  # a free plan is not a payment


# --- panel list + CSV + isolation ------------------------------------------
@pytest_asyncio.fixture
async def env(sm):
    Session = sm
    with all_tenants():
        async with Session() as s:
            cust = PanelUser(username="cust", password_hash=hash_password("pw"),
                             tenant_id=T_A, is_superadmin=False)
            s.add(cust)
            await s.commit()
            cust_id = cust.id
    ua = await _user(Session, T_A, 5001)
    ub = await _user(Session, T_B, 6001)
    with tenant_scope(T_A):
        async with Session() as s:
            await InvoiceService(s).record(user_id=ua, kind="topup", amount=11111, method="card", source_ref="a")
    with tenant_scope(T_B):
        async with Session() as s:
            await InvoiceService(s).record(user_id=ub, kind="plan", amount=22222, method="wallet", source_ref="a")
    from app.api.main import app

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield app, cust_id
    finally:
        app.dependency_overrides.clear()


async def _client(app, uid):
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    return client


async def test_panel_invoices_list_and_csv_scoped(env):
    app, cust_id = env
    client = await _client(app, cust_id)
    try:
        page = await client.get("/panel/invoices")
        assert page.status_code == 200
        assert "11,111" in page.text and "22,222" not in page.text  # only tenant A
        csv = await client.get("/panel/invoices/export.csv")
        assert csv.status_code == 200
        assert "text/csv" in csv.headers["content-type"]
        assert "11111" in csv.text and "22222" not in csv.text
    finally:
        await client.aclose()
