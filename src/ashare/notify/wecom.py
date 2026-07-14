"""企业微信群机器人 Markdown 消息构造与发送。"""

from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import parse_qs, urlparse

from ..models import AnalysisType, Stance
from ..result import Err, Ok, Result
from .base import NotificationPayload, Notifier

_BJ_TZ = timezone(timedelta(hours=8))
_WECOM_LIMIT = 4000
_STANCE = {
    Stance.BULLISH: ("🔴", "看多"),
    Stance.BEARISH: ("🟢", "看空"),
    Stance.NEUTRAL: ("⚪", "中性"),
}


def _esc(value) -> str:
    text = html.escape(str(value if value is not None else ""), quote=True)
    for char in ("\\", "`", "*", "[", "]", "#", "_"):
        text = text.replace(char, f"\\{char}")
    return text


def _fmt_time(ts: int) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, _BJ_TZ).strftime("%Y-%m-%d %H:%M")


def split_message(text: str, limit: int = _WECOM_LIMIT) -> list[str]:
    """按 UTF-8 字节拆分，优先在换行处切分且不丢失原文。"""
    if limit <= 0:
        raise ValueError("limit 必须大于 0")
    chunks, remaining = [], text
    while len(remaining.encode("utf-8")) > limit:
        cut = _byte_prefix_length(remaining, limit)
        newline = remaining.rfind("\n", 0, cut + 1)
        if newline >= 0:
            cut = newline + 1
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    chunks.append(remaining)
    return chunks


def _byte_prefix_length(text: str, limit: int) -> int:
    size = 0
    for index, char in enumerate(text):
        char_size = len(char.encode("utf-8"))
        if size + char_size > limit:
            return index
        size += char_size
    return len(text)


def _make_throttle(
    interval: float = 3.1,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[], None]:
    last_call: float | None = None

    def throttle() -> None:
        nonlocal last_call
        now = clock()
        if last_call is not None:
            delay = interval - (now - last_call)
            if delay > 0:
                sleep(delay)
                now = clock()
        last_call = now

    return throttle


def build_message(payload: NotificationPayload) -> str:
    """把通知领域数据渲染成企业微信支持的 Markdown 子集。"""
    video, analysis = payload.video, payload.analysis
    lines = [f"### 🎬 {_esc(video.title or '(无标题)')}"]
    meta = [f"👤 {_esc(video.author_name)}"] if video.author_name else []
    meta.append(f"🕐 {_fmt_time(video.publish_time)}")
    lines.extend([f"> {' · '.join(meta)}", f"[原视频]({video.video_url})"])
    if analysis and analysis.summary:
        lines.extend(["\n#### 📌 摘要", _esc(analysis.summary)])
    lines.extend(_analysis_lines(analysis))
    if payload.transcript_text:
        lines.extend(["\n#### 📝 口播原文", _esc(payload.transcript_text)])
    return "\n".join(lines)


def _analysis_lines(analysis) -> list[str]:
    if analysis and analysis.analysis_type == AnalysisType.GENERAL:
        lines = []
        if analysis.tags:
            lines.append(f"🏷 标签：{_esc('、'.join(analysis.tags))}")
        if analysis.content:
            lines.extend(["\n#### 💡 内容分析", _esc(analysis.content)])
        return lines
    lines = []
    if analysis and analysis.sectors:
        lines.append(f"🏷 板块：{_esc('、'.join(analysis.sectors))}")
    return [*lines, *_opinion_lines(analysis)]


def _opinion_lines(analysis) -> list[str]:
    opinions = analysis.opinions if analysis else []
    if not opinions:
        return ["\n#### 💡 主要观点", "未抽取到明确的行业/个股观点"]
    lines = ["\n#### 💡 主要观点"]
    for opinion in opinions:
        emoji, label = _STANCE.get(opinion.stance, ("⚪", "中性"))
        code = f"（{_esc(opinion.target_code)}）" if opinion.target_code else ""
        logic = f" — {_esc(opinion.logic)}" if opinion.logic else ""
        lines.append(f"{emoji} [{label}] {_esc(opinion.target_name)}{code}{logic}")
    return lines


class WeComNotifier(Notifier):
    channel = "wecom"

    def __init__(
        self,
        webhook_url: str,
        *,
        poster: Callable[[str, dict], dict] | None = None,
        throttle: Callable[[], None] | None = None,
    ):
        self._url = webhook_url
        self._key = parse_qs(urlparse(webhook_url).query).get("key", [""])[0]
        self._poster = poster or _httpx_poster
        self._throttle = throttle or _make_throttle()

    def send(self, payload: NotificationPayload) -> Result[None, str]:
        for chunk in split_message(build_message(payload)):
            result = self._post_chunk(chunk)
            if not result.is_ok():
                return result
        return Ok(None)

    def _post_chunk(self, chunk: str) -> Result[None, str]:
        payload = {"msgtype": "markdown", "markdown": {"content": chunk}}
        for attempt in range(2):
            self._throttle()
            try:
                data = self._poster(self._url, payload)
            except Exception as error:
                if attempt == 1:
                    return Err(f"企业微信发送失败：{type(error).__name__}")
                continue
            if not isinstance(data, dict):
                return Err("企业微信响应格式异常")
            if data.get("errcode") == 0:
                return Ok(None)
            return Err(
                f"企业微信发送失败（errcode={data.get('errcode')}）："
                f"{self._sanitize(data.get('errmsg', ''))}"
            )
        return Err("企业微信发送失败")

    def _sanitize(self, value) -> str:
        text = str(value)
        for secret in (self._url, self._key):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text


def _httpx_poster(url: str, payload: dict) -> dict:
    import httpx

    httpx_logger = logging.getLogger("httpx")
    old_level = httpx_logger.level
    httpx_logger.setLevel(max(old_level, logging.WARNING))
    try:
        response = httpx.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    finally:
        httpx_logger.setLevel(old_level)
