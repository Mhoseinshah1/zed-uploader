"""D1 unit tests — backup job lifecycle, prune, schedule, restore guard.

SQLite + monkeypatched pg_dump runner (no real subprocess).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.services import backup_service
from app.services.backup_service import BackupService


@pytest_asyncio.fixture
async def sqlite_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _ok_dump(tmp_path):
    async def fake(dsn, path):
        Path(path).write_text("-- dump")
        return True, None

    return fake


async def test_job_lifecycle_success(sqlite_maker, monkeypatch, tmp_path):
    monkeypatch.setattr(backup_service, "run_pg_dump", _ok_dump(tmp_path))
    async with sqlite_maker() as s:
        svc = BackupService(s, backup_dir=str(tmp_path))
        job = await svc.create_job(type_="manual", created_by_admin_id=42)
        assert job.status == "pending"
        assert (await svc.next_pending()).id == job.id

        await svc.run_job(job)
        assert job.status == "success"
        assert job.file_path and Path(job.file_path).exists()
        assert job.file_size and job.file_size > 0
        assert job.completed_at is not None
        assert await svc.next_pending() is None


async def test_failed_dump_records_error(sqlite_maker, monkeypatch, tmp_path):
    async def failing(dsn, path):
        return False, "pg_dump: connection refused"

    monkeypatch.setattr(backup_service, "run_pg_dump", failing)
    async with sqlite_maker() as s:
        svc = BackupService(s, backup_dir=str(tmp_path))
        job = await svc.create_job()
        await svc.run_job(job)
        assert job.status == "failed"
        assert "connection refused" in job.error_message
        assert job.file_path is None and job.completed_at is not None


async def test_prune_keeps_last_n(sqlite_maker, monkeypatch, tmp_path):
    monkeypatch.setattr(backup_service, "run_pg_dump", _ok_dump(tmp_path))
    async with sqlite_maker() as s:
        svc = BackupService(s, backup_dir=str(tmp_path))
        jobs = []
        for _ in range(5):
            job = await svc.create_job()
            await svc.run_job(job)
            jobs.append(job)

        removed = await svc.prune(keep=2)
        assert removed == 3
        remaining = await svc.list_jobs()
        assert len(remaining) == 2
        assert {j.id for j in remaining} == {jobs[3].id, jobs[4].id}  # newest kept
        # old files gone from disk, newest still present
        assert not Path(jobs[0].file_path).exists()
        assert Path(jobs[4].file_path).exists()


async def test_scheduled_due_logic(sqlite_maker):
    async with sqlite_maker() as s:
        svc = BackupService(s)
        assert await svc.due_scheduled("off") is False
        assert await svc.due_scheduled("daily") is True  # never ran

        job = await svc.create_job(type_="scheduled")
        assert await svc.due_scheduled("daily") is False  # just ran
        # age the job past a day -> due again
        job.created_at = datetime.now(timezone.utc) - timedelta(days=2)
        await s.commit()
        assert await svc.due_scheduled("daily") is True
        assert await svc.due_scheduled("weekly") is False  # < a week old


async def test_delete_job_removes_file(sqlite_maker, monkeypatch, tmp_path):
    monkeypatch.setattr(backup_service, "run_pg_dump", _ok_dump(tmp_path))
    async with sqlite_maker() as s:
        svc = BackupService(s, backup_dir=str(tmp_path))
        job = await svc.create_job()
        await svc.run_job(job)
        path = Path(job.file_path)
        assert await svc.delete_job(job.id) is True
        assert not path.exists()
        assert await svc.delete_job(9999) is False


async def test_panel_backups_owner_only():
    """No panel session -> bounced to login (download + restore + page)."""
    import httpx
    from httpx import ASGITransport

    from app.api.main import app

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for url in ("/panel/backups", "/panel/backups/1/download"):
            resp = await client.get(url, follow_redirects=False)
            assert resp.status_code == 302 and "/panel/login" in resp.headers["location"]
        resp = await client.post(
            "/panel/backups/1/restore", data={"confirm_filename": "x"},
            follow_redirects=False,
        )
        assert resp.status_code == 302 and "/panel/login" in resp.headers["location"]


async def test_restore_requires_exact_filename(monkeypatch, tmp_path):
    """Authenticated restore: wrong typed filename never runs psql; the exact
    filename does."""
    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm
    from sqlalchemy.ext.asyncio import create_async_engine as _cae
    from sqlalchemy.pool import StaticPool as _SP

    from app.api.main import app
    from app.core.redis_client import get_redis
    from app.db.session import get_session
    from app.models import PanelUser
    from app.panel import security
    from app.panel.session import COOKIE_NAME, SessionStore

    engine = _cae(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=_SP
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = _asm(engine, expire_on_commit=False)

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _override

    calls: list[str] = []

    async def fake_restore(dsn, path):
        calls.append(path)
        return True, None

    monkeypatch.setattr(backup_service, "run_pg_restore", fake_restore)
    monkeypatch.setattr(backup_service, "run_pg_dump", _ok_dump(tmp_path))

    try:
        async with Session() as s:
            s.add(PanelUser(username="own", password_hash=security.hash_password("x"), tenant_id=1, is_superadmin=True))
            await s.commit()
            uid = (await s.scalar(
                __import__("sqlalchemy").select(PanelUser.id)
            ))
            svc = BackupService(s, backup_dir=str(tmp_path))
            job = await svc.create_job()
            await svc.run_job(job)
            fname = Path(job.file_path).name

        csrf = security.generate_csrf()
        sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
        cookie = security.sign(sid)

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            client.cookies.set(COOKIE_NAME, cookie)
            # wrong filename -> rejected BEFORE any subprocess
            resp = await client.post(
                f"/panel/backups/{job.id}/restore",
                data={"confirm_filename": "wrong.sql", "csrf_token": csrf},
                follow_redirects=False,
            )
            assert resp.status_code == 302 and "error=confirm" in resp.headers["location"]
            assert calls == []
            # exact filename -> restore runs
            resp = await client.post(
                f"/panel/backups/{job.id}/restore",
                data={"confirm_filename": fname, "csrf_token": csrf},
                follow_redirects=False,
            )
            assert resp.status_code == 302 and "error=restored" in resp.headers["location"]
            assert calls == [job.file_path]
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
