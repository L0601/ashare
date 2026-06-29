"""基于 F2 的抖音抓取实现。

F2 是异步库，这里在同步接口内用独立事件循环驱动，向上层屏蔽异步细节。
所有外部调用都包到 Result 里：抖音风控/签名失效属可恢复错误，用值表达而非抛异常。

注意：真实抓取需在 config.yaml 填入有效登录 Cookie，且需联网，无法在单测中覆盖；
单测覆盖的是纯逻辑（sec_uid 解析、时间解析、分页拼装），见 tests/test_douyin_client.py。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import AppConfig
from ..models import Video
from ..result import Err, Ok, Result
from .base import DouyinClient

# 抖音 create_time 默认按东八区格式化为该字符串（见 f2.utils.timestamp_2_str）
_BJ_TZ = timezone(timedelta(hours=8))
_CREATE_TIME_FMT = "%Y-%m-%d %H-%M-%S"
_SEC_UID_RE = re.compile(r"/user/([\w\-]+)")
# 视频 id 可能出现在 /video/<id> 路径，或搜索/弹窗页的 modal_id=<id> 参数里
_AWEME_ID_RE = re.compile(r"(?:/video/|modal_id=)(\d+)")


def parse_create_time(value: Any) -> int:
    """把 F2 的发布时间（格式化字符串或原始时间戳）解析为 Unix 秒。"""
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value)
    if s.isdigit():
        return int(s)
    try:
        dt = datetime.strptime(s, _CREATE_TIME_FMT).replace(tzinfo=_BJ_TZ)
        return int(dt.timestamp())
    except ValueError:
        return 0


def extract_sec_uid(name_or_url: str) -> str:
    """从主页/分享链接里提取 sec_uid；非链接则原样返回（视为已是 sec_uid）。"""
    m = _SEC_UID_RE.search(name_or_url)
    if m:
        return m.group(1)
    return name_or_url.strip()


def extract_aweme_id(url_or_id: str) -> str:
    """从视频链接里提取 aweme_id；纯数字则原样返回。"""
    s = url_or_id.strip()
    m = _AWEME_ID_RE.search(s)
    if m:
        return m.group(1)
    return s


def _first(value: Any) -> Any:
    """F2 部分字段可能是嵌套列表（多档 CDN 地址），取第一个有效值。"""
    while isinstance(value, list) and value:
        value = value[0]
    return value


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else [value]


def build_videos_from_page(page: Any, sec_uid: str) -> list[Video]:
    """把一页 UserPostFilter 的并列字段拼装成 Video 列表（纯逻辑，可单测）。"""
    aweme_ids = _as_list(page.aweme_id)
    descs = _as_list(page.desc)
    cts = _as_list(page.create_time)
    addrs = _as_list(page.video_play_addr)
    covers = _as_list(page.cover)
    durs = _as_list(getattr(page, "video_duration", []))
    names = _as_list(getattr(page, "nickname", []))
    out: list[Video] = []
    for i, aid in enumerate(aweme_ids):
        if not aid:
            continue
        out.append(Video(
            aweme_id=str(aid),
            author_sec_uid=sec_uid,
            title=_pick(descs, i, ""),
            video_url=f"https://www.douyin.com/video/{aid}",
            publish_time=parse_create_time(_pick(cts, i, 0)),
            author_name=str(_first(_pick(names, i, "")) or ""),
            cover_url=str(_first(_pick(covers, i, "")) or ""),
            duration_ms=int(_first(_pick(durs, i, 0)) or 0),
            download_url=str(_first(_pick(addrs, i, "")) or ""),
        ))
    return out


def _pick(lst: list, i: int, default: Any) -> Any:
    return lst[i] if i < len(lst) else default


class F2DouyinClient(DouyinClient):
    """F2 抓取实现。"""

    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        self._kwargs = _build_f2_kwargs(cfg)

    # ---- 同步包装：每次起独立事件循环跑 F2 协程 ----
    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    def resolve_sec_uid(self, name_or_url: str) -> Result[str, str]:
        """支持三种输入：主页链接(/user/)、视频链接(/video/ 或 modal_id)、或直接 sec_uid。"""
        s = name_or_url.strip()
        if "/user/" in s:
            return Ok(extract_sec_uid(s))
        aid = extract_aweme_id(s)
        if aid.isdigit():
            # 从单条视频详情反查博主 sec_uid
            try:
                sec = self._run(self._sec_uid_of_video(aid))
                return Ok(sec) if sec else Err(f"视频 {aid} 详情未返回 sec_uid")
            except Exception as e:
                return Err(f"反查 sec_uid 失败：{e}")
        if "http" not in s and "/" not in s:
            return Ok(s)  # 视为已是 sec_uid
        return Err("无法解析 sec_uid，请提供博主主页链接(/user/)、视频链接或 sec_uid。")

    def list_latest(self, sec_uid: str, n: int) -> Result[list[Video], str]:
        try:
            # 多取一些缓冲：抖音会把置顶视频排在最前（不按时间），多取后按发布时间倒序，
            # 才能拿到"真正最新"的 n 条，避免置顶老视频占名额。
            raw = self._run(self._collect(sec_uid, n + 5))
            raw.sort(key=lambda v: v.publish_time, reverse=True)
            return Ok(raw[:n])
        except Exception as e:  # 网络/风控/签名失效等，均按可恢复错误返回
            return Err(f"获取视频列表失败：{e}")

    def get_download_url(self, aweme_id: str) -> Result[str, str]:
        try:
            url = self._run(self._one_download_url(aweme_id))
            return Ok(url) if url else Err(f"视频 {aweme_id} 无可用下载地址")
        except Exception as e:
            return Err(f"获取下载地址失败：{e}")

    def fetch_profile(self, sec_uid: str) -> Result[dict, str]:
        try:
            return Ok(self._run(self._profile(sec_uid)))
        except Exception as e:
            return Err(f"拉取博主资料失败：{e}")

    def _make_handler(self):
        """构造 F2 handler 并关闭 Bark 推送（我们不用，且它每个视频都会发外部请求）。"""
        from f2.apps.douyin.handler import DouyinHandler

        handler = DouyinHandler(self._kwargs)
        handler.enable_bark = False
        return handler

    async def _collect(self, sec_uid: str, n: int) -> list[Video]:
        handler = self._make_handler()
        out: list[Video] = []
        async for page in handler.fetch_user_post_videos(
            sec_uid, page_counts=min(n, 20), max_counts=n
        ):
            out.extend(build_videos_from_page(page, sec_uid))
            if len(out) >= n or not getattr(page, "has_more", False):
                break
        return out

    async def _one_download_url(self, aweme_id: str) -> str:
        handler = self._make_handler()
        detail = await handler.fetch_one_video(aweme_id)
        return str(_first(detail.video_play_addr) or "")

    async def _sec_uid_of_video(self, aweme_id: str) -> str:
        handler = self._make_handler()
        detail = await handler.fetch_one_video(aweme_id)
        return str(_first(detail.sec_user_id) or "")

    async def _profile(self, sec_uid: str) -> dict:
        handler = self._make_handler()
        p = await handler.fetch_user_profile(sec_uid)
        return {
            "nickname": str(_first(p.nickname) or ""),
            "aweme_count": int(_first(p.aweme_count) or 0),
        }


def _build_f2_kwargs(cfg: AppConfig) -> dict:
    """构造 F2 DouyinHandler 所需 kwargs。Cookie 与 UA 来自配置。"""
    return {
        "cookie": cfg.douyin.cookie,
        "headers": {
            "User-Agent": cfg.douyin.user_agent,
            "Referer": "https://www.douyin.com/",
        },
        "proxies": {"http://": None, "https://": None},
        "timeout": 15,
        "max_retries": 3,
        "max_connections": 4,
        "max_tasks": 4,
        "max_counts": cfg.douyin.latest_count,
        "page_counts": min(cfg.douyin.latest_count, 20),
        "path": cfg.storage.data_dir,
        "naming": "{create}_{desc}",
        "mode": "post",
        "folderize": True,
        "interval": "all",
        "cover": "no",
        "music": False,
        "lyric": False,
        "languages": {"name": "zh_CN"},
    }
