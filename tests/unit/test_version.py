"""E3 tests — versioning helpers, boot sync, update.sh syntax, and the
env-var docs coverage guarantee (every .env.example key is documented)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.version as version_mod
from app.core.version import (
    VERSION_SETTING_KEY,
    code_version,
    installed_version,
    is_newer,
    sync_version,
)
from app.models import Base
from app.services.bot_setting_service import BotSettingService

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest_asyncio.fixture
async def session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def test_code_version_reads_version_file():
    on_disk = (REPO_ROOT / "VERSION").read_text().strip()
    assert code_version() == on_disk
    assert re.fullmatch(r"\d+\.\d+\.\d+", on_disk)


def test_code_version_falls_back_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(version_mod, "VERSION_FILE", tmp_path / "nope")
    assert code_version() == "0.0.0"


def test_is_newer_semver_ordering():
    assert is_newer("1.0.1", "1.0.0")
    assert is_newer("1.10.0", "1.9.9")  # numeric, not lexicographic
    assert is_newer("2.0.0", "1.99.99")
    assert not is_newer("1.0.0", "1.0.0")
    assert not is_newer("0.9.0", "1.0.0")
    assert is_newer("1.0.0", "garbage")  # unparseable treated as 0


async def test_sync_version_records_and_never_downgrades(session_maker, monkeypatch, tmp_path):
    vfile = tmp_path / "VERSION"
    vfile.write_text("1.2.3\n")
    monkeypatch.setattr(version_mod, "VERSION_FILE", vfile)

    async with session_maker() as s:
        assert await installed_version(s) == "0.0.0"  # fresh install
        assert await sync_version(s) == "1.2.3"
        assert await installed_version(s) == "1.2.3"
        # same version again: no-op
        assert await sync_version(s) == "1.2.3"
        # DB already ahead of the code tree (e.g. rolled-back code): keep it
        await BotSettingService(s).set(VERSION_SETTING_KEY, "9.9.9")
        assert await sync_version(s) == "9.9.9"
        assert await installed_version(s) == "9.9.9"


def test_update_sh_syntax_and_version_output():
    script = REPO_ROOT / "update.sh"
    proc = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    body = script.read_text()
    assert "OLD_VERSION" in body and "NEW_VERSION" in body
    assert "ROLLBACK GUIDANCE" in body


def test_env_reference_covers_every_env_var():
    """docs/env-reference.md must document EVERY key in .env.example."""
    doc = (REPO_ROOT / "docs" / "env-reference.md").read_text(encoding="utf-8")
    keys = [
        line.split("=", 1)[0].strip()
        for line in (REPO_ROOT / ".env.example").read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    ]
    assert keys, ".env.example parsed to zero keys"
    missing = [k for k in keys if f"`{k}`" not in doc]
    assert not missing, f"env vars missing from docs/env-reference.md: {missing}"
    # settings-only / activation-server extras must be documented too
    for extra in ("LICENSE_FILE", "ACTIVATION_DB"):
        assert f"`{extra}`" in doc, f"{extra} missing from docs/env-reference.md"
