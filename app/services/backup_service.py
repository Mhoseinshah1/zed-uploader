"""BackupService — pg_dump/psql jobs with a DB-tracked lifecycle (D1).

The dump/restore subprocess runners are module-level functions so tests (and
operators) can intercept them; the service only manages job rows, file paths,
pruning, and scheduling. Dumps use ``--clean --if-exists`` so a restore over an
existing database works (it DROPs objects first — destructive by design).
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.backup_job import BackupJob

log = get_logger("backup")

BACKUP_DIR = "/backups"

# BotSetting keys (owner-configurable)
KEY_BACKUP_SCHEDULE = "backup_schedule"   # off | daily | weekly
KEY_BACKUP_KEEP = "backup_keep"
DEFAULT_BACKUP_KEEP = 7
_SCHEDULE_INTERVALS = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}


def _dsn() -> str:
    """DATABASE_URL for the pg client tools (strip the asyncpg driver)."""
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def run_pg_dump(dsn: str, path: str) -> tuple[bool, str | None]:
    """Run pg_dump; returns (ok, error_message). Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pg_dump", "--clean", "--if-exists", "--dbname", dsn, "--file", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0:
            return True, None
        return False, (stderr or b"").decode(errors="replace")[:500]
    except Exception as exc:  # binary missing / spawn failure
        return False, str(exc)[:500]


async def run_pg_restore(dsn: str, path: str) -> tuple[bool, str | None]:
    """Apply a plain-SQL dump with psql; returns (ok, error_message)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "psql", "--dbname", dsn, "--set", "ON_ERROR_STOP=1", "--file", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0:
            return True, None
        return False, (stderr or b"").decode(errors="replace")[:500]
    except Exception as exc:
        return False, str(exc)[:500]


class BackupService:
    def __init__(self, session: AsyncSession, backup_dir: str = BACKUP_DIR) -> None:
        self.session = session
        self.backup_dir = backup_dir

    # ------------------------------------------------------------------
    # job lifecycle
    # ------------------------------------------------------------------
    async def create_job(
        self, *, type_: str = "manual", created_by_admin_id: int | None = None
    ) -> BackupJob:
        job = BackupJob(type=type_, created_by_admin_id=created_by_admin_id)
        self.session.add(job)
        await self.session.commit()
        return job

    async def next_pending(self) -> BackupJob | None:
        return await self.session.scalar(
            select(BackupJob)
            .where(BackupJob.status == "pending")
            .order_by(BackupJob.id)
            .limit(1)
        )

    async def run_job(self, job: BackupJob) -> BackupJob:
        """pending -> running -> success|failed, recording file/error."""
        job.status = "running"
        await self.session.commit()

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = str(Path(self.backup_dir) / f"backup-{stamp}-job{job.id}.sql")
        try:
            Path(self.backup_dir).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            job.status = "failed"
            job.error_message = f"backup dir: {exc}"[:500]
            job.completed_at = datetime.now(timezone.utc)
            await self.session.commit()
            return job

        ok, error = await run_pg_dump(_dsn(), path)
        job.completed_at = datetime.now(timezone.utc)
        if ok:
            job.status = "success"
            job.file_path = path
            try:
                job.file_size = os.path.getsize(path)
            except OSError:
                job.file_size = None
            log.info("backup_done", job_id=job.id, path=path, size=job.file_size)
        else:
            job.status = "failed"
            job.error_message = error
            log.error("backup_failed", job_id=job.id, error=error)
        await self.session.commit()
        return job

    # ------------------------------------------------------------------
    # listing / prune / schedule
    # ------------------------------------------------------------------
    async def list_jobs(self, *, limit: int = 30) -> list[BackupJob]:
        result = await self.session.scalars(
            select(BackupJob).order_by(BackupJob.id.desc()).limit(limit)
        )
        return list(result.all())

    async def get(self, job_id: int) -> BackupJob | None:
        return await self.session.get(BackupJob, job_id)

    async def delete_job(self, job_id: int) -> bool:
        """Remove a job row and its dump file (if any)."""
        job = await self.get(job_id)
        if job is None:
            return False
        if job.file_path:
            try:
                os.remove(job.file_path)
            except OSError:
                pass
        await self.session.delete(job)
        await self.session.commit()
        return True

    async def prune(self, keep: int) -> int:
        """Delete the oldest SUCCESS backups (rows + files) beyond ``keep``."""
        rows = list(
            await self.session.scalars(
                select(BackupJob)
                .where(BackupJob.status == "success")
                .order_by(BackupJob.id.desc())
            )
        )
        removed = 0
        for job in rows[max(keep, 0):]:
            if job.file_path:
                try:
                    os.remove(job.file_path)
                except OSError:
                    pass
            await self.session.delete(job)
            removed += 1
        if removed:
            await self.session.commit()
            log.info("backups_pruned", removed=removed, kept=keep)
        return removed

    async def due_scheduled(self, schedule: str) -> bool:
        """True when the schedule warrants a new scheduled job now."""
        interval = _SCHEDULE_INTERVALS.get(schedule)
        if interval is None:
            return False
        last = await self.session.scalar(
            select(BackupJob.created_at)
            .where(BackupJob.type == "scheduled")
            .order_by(BackupJob.id.desc())
            .limit(1)
        )
        if last is None:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last >= interval
