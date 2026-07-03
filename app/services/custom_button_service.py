"""CustomButtonService (J8) — tenant reply-keyboard buttons + action whitelist.

``action`` buttons map ONLY to entries of ``ACTION_WHITELIST`` — named,
code-defined behaviors. An unknown action value is stored but does nothing at
runtime (and the panel refuses to create one). Arbitrary code can never run.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_button import BUTTON_TYPES, CustomButton

# action key -> implemented in app/bot/handlers/custom_buttons.py
ACTION_WHITELIST = ("help", "wallet")


class CustomButtonService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self) -> list[CustomButton]:
        rows = await self.session.scalars(
            select(CustomButton)
            .where(CustomButton.is_active.is_(True))
            .order_by(CustomButton.sort_order, CustomButton.id)
        )
        return list(rows.all())

    async def list_all(self) -> list[CustomButton]:
        rows = await self.session.scalars(
            select(CustomButton).order_by(CustomButton.sort_order, CustomButton.id)
        )
        return list(rows.all())

    async def by_label(self, label: str) -> CustomButton | None:
        return await self.session.scalar(
            select(CustomButton).where(
                CustomButton.label == label, CustomButton.is_active.is_(True)
            )
        )

    async def create(
        self, label: str, type_: str, value: str, sort_order: int = 0
    ) -> CustomButton | None:
        label = label.strip()[:64]
        if not label or type_ not in BUTTON_TYPES:
            return None
        if type_ == "action" and value not in ACTION_WHITELIST:
            return None  # whitelist enforced at creation too
        if type_ == "url" and not value.startswith(("https://", "http://", "tg://")):
            return None
        button = CustomButton(
            label=label, type=type_, value=value.strip(), sort_order=sort_order
        )
        self.session.add(button)
        await self.session.commit()
        return button

    async def toggle(self, button_id: int) -> bool:
        button = await self.session.get(CustomButton, button_id)
        if button is None:
            return False
        button.is_active = not button.is_active
        await self.session.commit()
        return True

    async def delete(self, button_id: int) -> bool:
        button = await self.session.get(CustomButton, button_id)
        if button is None:
            return False
        await self.session.delete(button)
        await self.session.commit()
        return True
