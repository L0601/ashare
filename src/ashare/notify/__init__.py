"""推送层：把分析结果发送到 Telegram、企业微信等渠道。"""

from .base import (
    FakeNotifier,
    NoopNotifier,
    NotificationPayload,
    Notifier,
    make_notifier,
    make_notifiers,
)
from .wecom import WeComNotifier

__all__ = [
    "Notifier", "NotificationPayload", "FakeNotifier", "NoopNotifier",
    "WeComNotifier", "make_notifier", "make_notifiers",
]
