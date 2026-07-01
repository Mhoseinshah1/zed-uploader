"""FSM state groups for multi-step admin inputs."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class MediaEdit(StatesGroup):
    waiting_limit = State()
    waiting_autodelete = State()
    waiting_caption = State()


class SettingsEdit(StatesGroup):
    waiting_default_autodelete = State()
