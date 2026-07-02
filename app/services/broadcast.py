"""Broadcast service — a per-recipient ledger that makes sending exactly-once.

The DB rows are the source of truth (no Redis queue): ``create_job`` snapshots
every current user as a ``pending`` recipient, and the worker drains ``pending``
rows page by page, moving each to ``sent``/``failed``/``blocked`` exactly once.
A restart just re-reads the remaining ``pending`` rows, so nothing is re-sent.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import require_tenant
from app.models.broadcast import BroadcastJob, BroadcastRecipient
from app.models.user import User

PAGE_SIZE = 100
SEND_DELAY = 0.05  # ~20 messages/sec, safely under Telegram limits
_SNAPSHOT_CHUNK = 1000


async def audience_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count(User.id))) or 0)


async def create_job(
    session: AsyncSession,
    *,
    from_chat_id: int | None = None,
    message_id: int | None = None,
    text: str | None = None,
    created_by: int | None = None,
) -> BroadcastJob:
    """Create a job and snapshot ALL current users as ``pending`` recipients.

    The job + recipient snapshot commit together; ``total`` records how many
    recipients were captured.
    """
    job = BroadcastJob(
        from_chat_id=from_chat_id,
        message_id=message_id,
        text=text,
        created_by=created_by,
        status="pending",
    )
    session.add(job)
    await session.flush()  # assign job.id before snapshotting recipients

    total = 0
    # Core bulk insert bypasses the ORM before_flush tenant stamp, so set
    # tenant_id explicitly (the recipients belong to the current tenant, same as
    # the users just selected under this tenant context).
    tenant_id = require_tenant()
    rows = (await session.execute(select(User.id, User.telegram_id).order_by(User.id))).all()
    batch: list[dict] = []
    for uid, tg in rows:
        batch.append(
            {
                "tenant_id": tenant_id,
                "broadcast_id": job.id,
                "user_id": uid,
                "telegram_id": tg,
                "status": "pending",
            }
        )
        if len(batch) >= _SNAPSHOT_CHUNK:
            await session.execute(insert(BroadcastRecipient), batch)
            total += len(batch)
            batch = []
    if batch:
        await session.execute(insert(BroadcastRecipient), batch)
        total += len(batch)

    job.total = total
    await session.commit()
    return job


# ---------------------------------------------------------------------------
# worker-side helpers (DB is the source of truth)
# ---------------------------------------------------------------------------
async def next_job_tenant(session: AsyncSession) -> int | None:
    """Tenant id of the oldest unfinished broadcast job across ALL tenants.

    Called under the all_tenants context so the multi-tenant worker can then
    ``set_tenant`` and process that job with the right bot. Returns None when
    no job is pending anywhere.
    """
    return await session.scalar(
        select(BroadcastJob.tenant_id)
        .where(BroadcastJob.status.in_(("pending", "running")))
        .order_by(BroadcastJob.id)
        .limit(1)
    )


async def claim_next_job(session: AsyncSession) -> BroadcastJob | None:
    """Return the oldest unfinished job, marking it ``running``.

    A ``running`` job (from an earlier page or a crash) is picked up again, so
    processing resumes where it left off.
    """
    job = await session.scalar(
        select(BroadcastJob)
        .where(BroadcastJob.status.in_(("pending", "running")))
        .order_by(BroadcastJob.id)
        .limit(1)
    )
    if job is None:
        return None
    if job.status != "running":
        job.status = "running"
        await session.commit()
    return job


async def next_pending_page(
    session: AsyncSession, job_id: int, limit: int = PAGE_SIZE
) -> list[BroadcastRecipient]:
    result = await session.scalars(
        select(BroadcastRecipient)
        .where(
            BroadcastRecipient.broadcast_id == job_id,
            BroadcastRecipient.status == "pending",
        )
        .order_by(BroadcastRecipient.id)
        .limit(limit)
    )
    return list(result.all())


async def refresh_job_counts(session: AsyncSession, job_id: int) -> None:
    """Recompute job counts from the recipient ledger (always consistent)."""
    rows = (
        await session.execute(
            select(BroadcastRecipient.status, func.count())
            .where(BroadcastRecipient.broadcast_id == job_id)
            .group_by(BroadcastRecipient.status)
        )
    ).all()
    counts = {status: int(n) for status, n in rows}
    job = await session.get(BroadcastJob, job_id)
    if job is None:
        return
    job.sent = counts.get("sent", 0)
    job.failed = counts.get("failed", 0)
    job.blocked = counts.get("blocked", 0)
    job.total = sum(counts.values())


async def finalize_job(session: AsyncSession, job_id: int) -> tuple[int, int, int]:
    """Mark a fully-drained job done (or failed if nothing sent); return counts."""
    await refresh_job_counts(session, job_id)
    job = await session.get(BroadcastJob, job_id)
    if job is None:
        return (0, 0, 0)
    job.status = "done" if (job.sent > 0 or job.total == 0) else "failed"
    job.completed_at = datetime.now(timezone.utc)
    await session.commit()
    return (job.sent, job.failed, job.blocked)


async def retry_failed(session: AsyncSession, job_id: int) -> int:
    """Re-queue ONLY ``failed`` recipients; ``sent`` rows are never touched."""
    result = await session.execute(
        update(BroadcastRecipient)
        .where(
            BroadcastRecipient.broadcast_id == job_id,
            BroadcastRecipient.status == "failed",
        )
        .values(status="pending", error_message=None, sent_at=None)
    )
    requeued = int(result.rowcount or 0)
    if requeued:
        job = await session.get(BroadcastJob, job_id)
        if job is not None:
            job.status = "pending"
            job.completed_at = None
    await session.commit()
    return requeued


async def list_jobs(session: AsyncSession, *, limit: int = 20) -> list[BroadcastJob]:
    result = await session.scalars(
        select(BroadcastJob).order_by(BroadcastJob.id.desc()).limit(limit)
    )
    return list(result.all())
