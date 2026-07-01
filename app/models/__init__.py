"""Import every model so Alembic autogenerate sees the full metadata."""
from __future__ import annotations

from app.db.base import Base
from app.models.admin import Admin
from app.models.channel import RequiredChannel
from app.models.download_log import DownloadLog
from app.models.media import Media
from app.models.media_file import MediaFile
from app.models.settings import BotSetting, FeatureFlag
from app.models.user import User

__all__ = [
    "Base",
    "User",
    "Admin",
    "Media",
    "MediaFile",
    "DownloadLog",
    "BotSetting",
    "FeatureFlag",
    "RequiredChannel",
]
