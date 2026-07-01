"""WalletService — the ONLY writer of ``users.balance``.

Every balance change happens inside ``_apply`` under a row lock and writes a
ledger row + the cached balance atomically, so the invariant
``SUM(wallet_transactions.amount) == users.balance`` always holds.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.user import User
from app.models.wallet import WalletTransaction

log = get_logger("wallet")


class InsufficientFunds(Exception):
    """Raised when a debit would drive the balance negative."""


class WalletService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _apply(
        self,
        user_id: int,
        amount: int,
        ttype: str,
        *,
        reference: str | None = None,
        description: str | None = None,
    ) -> int:
        # Lock the user row so concurrent credits/debits serialize.
        user = await self.session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        if user is None:
            raise ValueError("user not found")
        new_balance = user.balance + amount
        if new_balance < 0:
            raise InsufficientFunds()
        user.balance = new_balance
        self.session.add(
            WalletTransaction(
                user_id=user_id,
                amount=amount,
                type=ttype,
                balance_after=new_balance,
                reference=reference,
                description=description,
            )
        )
        await self.session.commit()
        return new_balance

    async def credit(
        self, user_id: int, amount: int, ttype: str = "deposit", **kw
    ) -> int:
        new_balance = await self._apply(user_id, abs(amount), ttype, **kw)
        log.info("wallet_credit", user_id=user_id, amount=abs(amount), balance=new_balance)
        return new_balance

    async def debit(
        self, user_id: int, amount: int, ttype: str = "purchase", **kw
    ) -> int:
        new_balance = await self._apply(user_id, -abs(amount), ttype, **kw)
        log.info("wallet_debit", user_id=user_id, amount=abs(amount), balance=new_balance)
        return new_balance

    async def balance(self, user_id: int) -> int:
        return int(
            await self.session.scalar(select(User.balance).where(User.id == user_id))
            or 0
        )

    async def last_transactions(
        self, user_id: int, limit: int = 10
    ) -> list[WalletTransaction]:
        result = await self.session.scalars(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == user_id)
            .order_by(WalletTransaction.id.desc())
            .limit(limit)
        )
        return list(result.all())
