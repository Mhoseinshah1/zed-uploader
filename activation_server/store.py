"""Activation-server store: licenses + installs (its own SQLite via SQLAlchemy).

Seat model: each distinct fingerprint that activates a key occupies one seat,
capped by ``allowed_install_count``. UNIQUE(key, fingerprint) makes
re-activation of a bound fingerprint idempotent (it never consumes a seat).

Response contract (consumed by the main app's LicenseService):
  {"ok": bool, "status": "active|expired|revoked|seat_limit|unknown",
   "expires_at": iso|None, "allowed_install_count": int, "seats_used": int}
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ActivationBase(DeclarativeBase):
    pass


class License(ActivationBase):
    __tablename__ = "licenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    allowed_install_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Install(ActivationBase):
    __tablename__ = "installs"
    __table_args__ = (UniqueConstraint("key", "fingerprint", name="uq_install_seat"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(
        ForeignKey("licenses.key", ondelete="CASCADE"), index=True, nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class ActivationStore:
    def __init__(self, db_path: str) -> None:
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(ActivationBase.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    # ------------------------------------------------------------------
    # seller operations (CLI)
    # ------------------------------------------------------------------
    async def issue(self, key: str, seats: int, days: int | None) -> License:
        """Create (or refresh) a key with N seats, optionally expiring."""
        async with self.sessions() as s:
            row = await s.scalar(select(License).where(License.key == key))
            expires = (
                datetime.now(timezone.utc) + timedelta(days=days) if days else None
            )
            if row is None:
                row = License(
                    key=key, status="active",
                    allowed_install_count=max(1, seats), expires_at=expires,
                )
                s.add(row)
            else:
                row.status = "active"
                row.allowed_install_count = max(1, seats)
                row.expires_at = expires
            await s.commit()
            return row

    async def revoke(self, key: str) -> bool:
        async with self.sessions() as s:
            row = await s.scalar(select(License).where(License.key == key))
            if row is None:
                return False
            row.status = "revoked"
            await s.commit()
            return True

    async def list_keys(self) -> list[tuple[str, str, str, int, int]]:
        """[(key, status, expires, seats_used, seats_total)] for the CLI."""
        async with self.sessions() as s:
            rows = list(await s.scalars(select(License).order_by(License.id)))
            out = []
            for lic in rows:
                used = int(
                    await s.scalar(
                        select(func.count(Install.id)).where(Install.key == lic.key)
                    )
                    or 0
                )
                out.append(
                    (
                        lic.key,
                        lic.status,
                        lic.expires_at.date().isoformat() if lic.expires_at else "-",
                        used,
                        lic.allowed_install_count,
                    )
                )
            return out

    # ------------------------------------------------------------------
    # protocol operations
    # ------------------------------------------------------------------
    @staticmethod
    def _license_state(lic: License | None) -> str:
        if lic is None:
            return "unknown"
        if lic.status == "revoked":
            return "revoked"
        expires = _aware(lic.expires_at)
        if expires is not None and expires <= datetime.now(timezone.utc):
            return "expired"
        return "active"

    @staticmethod
    def _payload(ok: bool, status: str, lic: License | None, seats_used: int) -> dict:
        return {
            "ok": ok,
            "status": status,
            "expires_at": (
                lic.expires_at.isoformat() if lic and lic.expires_at else None
            ),
            "allowed_install_count": lic.allowed_install_count if lic else 0,
            "seats_used": seats_used,
        }

    async def _seats_used(self, s: AsyncSession, key: str) -> int:
        return int(
            await s.scalar(select(func.count(Install.id)).where(Install.key == key))
            or 0
        )

    async def activate(self, key: str, fingerprint: str) -> dict:
        async with self.sessions() as s:
            lic = await s.scalar(select(License).where(License.key == key))
            state = self._license_state(lic)
            if state != "active":
                used = await self._seats_used(s, key) if lic else 0
                return self._payload(False, state, lic, used)

            install = await s.scalar(
                select(Install).where(
                    Install.key == key, Install.fingerprint == fingerprint
                )
            )
            if install is not None:
                # idempotent re-activation of a bound seat
                install.last_seen = datetime.now(timezone.utc)
                await s.commit()
                used = await self._seats_used(s, key)
                return self._payload(True, "active", lic, used)

            used = await self._seats_used(s, key)
            if used >= lic.allowed_install_count:
                return self._payload(False, "seat_limit", lic, used)
            s.add(Install(key=key, fingerprint=fingerprint))
            await s.commit()
            return self._payload(True, "active", lic, used + 1)

    async def check(self, key: str, fingerprint: str) -> dict:
        async with self.sessions() as s:
            lic = await s.scalar(select(License).where(License.key == key))
            state = self._license_state(lic)
            used = await self._seats_used(s, key) if lic else 0
            if state != "active":
                return self._payload(False, state, lic, used)
            install = await s.scalar(
                select(Install).where(
                    Install.key == key, Install.fingerprint == fingerprint
                )
            )
            if install is None:
                # active key but this server never activated -> not a seat
                return self._payload(False, "unknown", lic, used)
            install.last_seen = datetime.now(timezone.utc)
            await s.commit()
            return self._payload(True, "active", lic, used)
