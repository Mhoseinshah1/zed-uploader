"""FSM state groups for multi-step admin inputs."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class MediaEdit(StatesGroup):
    waiting_limit = State()
    waiting_autodelete = State()
    waiting_caption = State()


class SettingsEdit(StatesGroup):
    waiting_default_autodelete = State()


# --- Phase 2 ---------------------------------------------------------------
class Upload(StatesGroup):
    collecting = State()  # batch/multi-file upload


class ChannelAdd(StatesGroup):
    waiting = State()  # @username or forwarded channel message


class AdminAdd(StatesGroup):
    waiting = State()  # numeric id or forwarded user message


class Broadcast(StatesGroup):
    waiting_message = State()
    confirming = State()


# --- Phase 3 ---------------------------------------------------------------
class Topup(StatesGroup):
    waiting_amount = State()
    waiting_receipt = State()


class SellEdit(StatesGroup):
    waiting_card = State()
    waiting_holder = State()
    waiting_price = State()      # data: plan key
    waiting_duration = State()   # data: plan key
