"""Telegram 推送实现 + 消息构造。

- build_message：纯函数，把视频/转写/分析拼成 HTML 消息，可单测。
- TelegramNotifier：httpx 调 sendMessage，HTML 格式，超 4096 自动按行拆条，超时 + 轻量重试。
  HTTP 发送通过 poster 注入，测试时可传 fake。
"""

from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from ..models import Analysis, Stance, Video
from ..result import Err, Ok, Result
from .base import NotificationPayload, Notifier

_BJ_TZ = timezone(timedelta(hours=8))
_TG_LIMIT = 4000  # 留余量（Telegram 上限 4096）
_STANCE = {
    Stance.BULLISH: ("🔴", "看多"),
    Stance.BEARISH: ("🟢", "看空"),
    Stance.NEUTRAL: ("⚪", "中性"),
}


def _esc(s) -> str:
    """转义 Telegram HTML 特殊字符。"""
    return html.escape(str(s if s is not None else ""))


def _fmt_time(ts: int) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, _BJ_TZ).strftime("%Y-%m-%d %H:%M")


def build_message(video: Video, transcript_text: str, analysis: Analysis | None) -> str:
    """拼装 HTML 推送消息（作者/链接/时间/摘要/板块/观点/原文）。纯函数。"""
    lines = [f"🎬 <b>{_esc(video.title or '(无标题)')}</b>"]
    meta = []
    if video.author_name:
        meta.append(f"👤 {_esc(video.author_name)}")
    meta.append(f"🕐 {_fmt_time(video.publish_time)}")
    lines.append("  ·  ".join(meta))
    lines.append(f'🔗 <a href="{_esc(video.video_url)}">原视频</a>')

    if analysis and analysis.summary:
        lines.append(f"\n📌 <b>摘要</b>\n{_esc(analysis.summary)}")
    if analysis and analysis.sectors:
        lines.append(f"🏷 板块：{_esc('、'.join(analysis.sectors))}")

    opinions = analysis.opinions if analysis else []
    if opinions:
        lines.append("\n💡 <b>主要观点</b>")
        for o in opinions:
            emoji, label = _STANCE.get(o.stance, ("⚪", "中性"))
            code = f"（{_esc(o.target_code)}）" if o.target_code else ""
            logic = f" — {_esc(o.logic)}" if o.logic else ""
            lines.append(f"{emoji} [{label}] {_esc(o.target_name)}{code}{logic}")
    else:
        lines.append("\n💡 未抽取到明确的行业/个股观点")

    if transcript_text:
        lines.append(f"\n📝 <b>口播原文</b>\n{_esc(transcript_text)}")
    return "\n".join(lines)


def split_message(text: str, limit: int = _TG_LIMIT) -> list[str]:
    """按行累积拆成 ≤limit 的多段；单行超长则硬切。纯函数，可单测。"""
    chunks, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                 # 单行就超长，硬切
            if buf:
                chunks.append(buf); buf = ""
            chunks.append(line[:limit]); line = line[limit:]
        add = (buf + "\n" + line) if buf else line
        if len(add) > limit:
            chunks.append(buf); buf = line
        else:
            buf = add
    if buf:
        chunks.append(buf)
    return chunks or [""]


class TelegramNotifier(Notifier):
    channel = "telegram"

    def __init__(self, bot_token: str, chat_id: str, *,
                 poster: Callable[[str, dict], dict] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._poster = poster or _httpx_poster
        self._sleep = sleep

    def enabled(self) -> bool:
        return True

    def send(self, message: NotificationPayload | str) -> Result[None, str]:
        text = _render(message)
        chunks = split_message(text)
        for i, chunk in enumerate(chunks):
            res = self._post(chunk)
            if not res.is_ok():
                return res
            if i < len(chunks) - 1:
                self._sleep(0.5)   # 多条之间稍歇，避免触发限频
        return Ok(None)

    def _post(self, text: str) -> Result[None, str]:
        payload = {"chat_id": self._chat_id, "text": text,
                   "parse_mode": "HTML", "disable_web_page_preview": True}
        last = ""
        for attempt in range(2):   # 轻量重试一次
            try:
                data = self._poster(self._url, payload)
                if data.get("ok"):
                    return Ok(None)
                last = str(data)
            except Exception as e:
                last = str(e)
            self._sleep(1)
        return Err(f"Telegram 发送失败：{last}")


def _render(message: NotificationPayload | str) -> str:
    if isinstance(message, str):
        return message
    return build_message(message.video, message.transcript_text, message.analysis)


def _httpx_poster(url: str, payload: dict) -> dict:
    import httpx

    httpx_logger = logging.getLogger("httpx")
    old_level = httpx_logger.level
    httpx_logger.setLevel(max(old_level, logging.WARNING))
    try:
        resp = httpx.post(url, json=payload, timeout=10)
    finally:
        httpx_logger.setLevel(old_level)
    return resp.json()
