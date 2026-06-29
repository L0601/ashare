"""推送层：把分析结果发到 Telegram（接口 + 实现 + Fake/Noop）。"""

from .base import FakeNotifier, NoopNotifier, Notifier, make_notifier

__all__ = ["Notifier", "FakeNotifier", "NoopNotifier", "make_notifier"]
