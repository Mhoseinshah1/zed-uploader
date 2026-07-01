"""Migration audit (REAL Postgres): linear head, clean upgrade, no model drift."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import TEST_DATABASE_URL, requires_pg

pytestmark = requires_pg

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic(*args: str):
    env = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL or "",
        "BOT_TOKEN": "x",
        "ADMIN_IDS": "1",
        "API_KEY": "k",
        "WEBHOOK_SECRET": "s",
        "SESSION_SECRET": "s",
        "REDIS_URL": "redis://localhost:6379/0",
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


async def _clean_schema() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


def test_single_linear_head():
    result = _alembic("heads")
    assert result.returncode == 0, result.stderr
    heads = [ln for ln in result.stdout.splitlines() if "(head)" in ln]
    assert len(heads) == 1, f"expected one head, got: {result.stdout}"
    assert "0007_user_uploads" in heads[0]


async def test_clean_upgrade_and_no_drift():
    await _clean_schema()

    upgrade = _alembic("upgrade", "head")
    assert upgrade.returncode == 0, f"upgrade failed:\n{upgrade.stdout}\n{upgrade.stderr}"

    # alembic check compares the mapped models against the migrated schema.
    check = _alembic("check")
    combined = check.stdout + check.stderr
    assert check.returncode == 0, f"model/schema drift detected:\n{combined}"
    assert "No new upgrade operations detected" in combined
