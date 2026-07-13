"""多渠道推送接口、领域载荷、Fake / Noop 实现与工厂。

pipeline 只依赖接口和中立载荷；生产工厂返回启用渠道，测试用 FakeNotifier。
NoopNotifier 保留给兼容调用。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from ..config import AppConfig, TelegramConfig
from ..models import Analysis, Video
from ..result import Ok, Result


@dataclass(frozen=True)
class NotificationPayload:
    video: Video
    transcript_text: str
    analysis: Analysis | None


class Notifier(abc.ABC):
    channel = "generic"

    @abc.abstractmethod
    def send(self, payload: NotificationPayload) -> Result[None, str]:
        """发送一条消息（实现负责处理超长拆分等）。"""

    def enabled(self) -> bool:
        return True


class NoopNotifier(Notifier):
    """未配置/未启用时使用：直接成功，不发送。"""

    channel = "noop"

    def send(self, payload) -> Result[None, str]:
        return Ok(None)

    def enabled(self) -> bool:
        return False


class FakeNotifier(Notifier):
    """测试用：把消息存进 sent 列表。"""

    def __init__(self, channel: str = "fake"):
        self.channel = channel
        self.sent: list = []

    def send(self, payload) -> Result[None, str]:
        self.sent.append(payload)
        return Ok(None)


def make_notifier(cfg: TelegramConfig) -> Notifier:
    """按配置创建推送器：启用且 token/chat_id 齐全才真正发，否则空转。"""
    if cfg.enabled and cfg.bot_token and cfg.chat_id:
        from .telegram import TelegramNotifier

        return TelegramNotifier(cfg.bot_token, cfg.chat_id)
    return NoopNotifier()


def make_notifiers(cfg: AppConfig) -> list[Notifier]:
    """按应用配置装配全部启用且配置完整的推送渠道。"""
    notifiers = []
    telegram = make_notifier(cfg.telegram)
    if telegram.enabled():
        notifiers.append(telegram)
    if cfg.wecom.enabled:
        if not _valid_wecom_url(cfg.wecom.webhook_url):
            raise ValueError("企业微信 webhook_url 格式不合法")
        from .wecom import WeComNotifier

        notifiers.append(WeComNotifier(cfg.wecom.webhook_url))
    return notifiers


def _valid_wecom_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "qyapi.weixin.qq.com"
        and parsed.path == "/cgi-bin/webhook/send"
        and bool(parse_qs(parsed.query).get("key", [""])[0])
    )
