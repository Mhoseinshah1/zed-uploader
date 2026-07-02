"""FSM state groups for multi-step admin inputs."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class MediaEdit(StatesGroup):
    waiting_limit = State()
    waiting_autodelete = State()
    waiting_caption = State()
    waiting_password = State()  # data: media_id, page — set/change/remove
    waiting_folder = State()    # data: media_id, page — picking a target folder


class FolderEdit(StatesGroup):
    waiting_name = State()    # data: parent_id — creating a folder/subfolder
    waiting_rename = State()  # data: folder_id — renaming a folder


class Delivery(StatesGroup):
    waiting_password = State()  # data: code — a viewer entering a file password


class Review(StatesGroup):
    waiting_reason = State()  # data: media_id, page — admin rejecting an upload


class Search(StatesGroup):
    active = State()  # data: query — holds the last query for paginated browsing


class AdCreate(StatesGroup):
    waiting_title = State()
    waiting_text = State()      # data: title
    waiting_placement = State()  # data: title, text — picked via inline buttons


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
