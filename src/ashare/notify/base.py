"""推送接口、Fake / Noop 实现与工厂。

推送抽象成接口，pipeline 只依赖接口；未启用时用 NoopNotifier 空转，测试用 FakeNotifier。
"""

from __future__ import annotations

import abc

from ..config import TelegramConfig
from ..result import Ok, Result


class Notifier(abc.ABC):
    @abc.abstractmethod
    def send(self, text: str) -> Result[None, str]:
        """发送一条消息（实现负责处理超长拆分等）。"""

    def enabled(self) -> bool:
        return True


class NoopNotifier(Notifier):
    """未配置/未启用时使用：直接成功，不发送。"""

    def send(self, text: str) -> Result[None, str]:
        return Ok(None)

    def enabled(self) -> bool:
        return False


class FakeNotifier(Notifier):
    """测试用：把消息存进 sent 列表。"""

    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> Result[None, str]:
        self.sent.append(text)
        return Ok(None)


def make_notifier(cfg: TelegramConfig) -> Notifier:
    """按配置创建推送器：启用且 token/chat_id 齐全才真正发，否则空转。"""
    if cfg.enabled and cfg.bot_token and cfg.chat_id:
        from .telegram import TelegramNotifier

        return TelegramNotifier(cfg.bot_token, cfg.chat_id)
    return NoopNotifier()
