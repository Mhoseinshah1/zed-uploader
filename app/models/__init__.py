"""Import every model so Alembic autogenerate sees the full metadata."""
from __future__ import annotations

from app.db.base import Base
from app.models.ad import Ad
from app.models.admin import Admin
from app.models.backup_job import BackupJob
from app.models.broadcast import BroadcastJob, BroadcastRecipient
from app.models.channel import RequiredChannel
from app.models.download_log import DownloadLog
from app.models.folder import Folder
from app.models.media import Media
from app.models.media_file import MediaFile
from app.models.panel import PanelAudit, PanelUser
from app.models.payment import Payment
from app.models.payment_provider import PaymentProviderConfig
from app.models.plan import Plan
from app.models.settings import BotSetting, FeatureFlag
from app.models.subscription import Subscription
from app.models.user import User
from app.models.wallet import WalletTransaction

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
    "Plan",
    "Subscription",
    "WalletTransaction",
    "Payment",
    "PanelUser",
    "PanelAudit",
    "BroadcastJob",
    "BroadcastRecipient",
    "Folder",
    "PaymentProviderConfig",
    "Ad",
    "BackupJob",
]
