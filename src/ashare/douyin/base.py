"""抓取层接口与测试用 Fake。

把抖音抓取抽象成接口，pipeline 只依赖接口；真实实现是 F2，
测试/离线开发用 FakeDouyinClient。这样抓取层失效也不影响其余链路开发与测试。
"""

from __future__ import annotations

import abc

from ..models import Video
from ..result import Err, Ok, Result


class DouyinClient(abc.ABC):
    """抖音抓取统一接口（同步）。"""

    @abc.abstractmethod
    def resolve_sec_uid(self, name_or_url: str) -> Result[str, str]:
        """把博主昵称或主页/分享链接解析为 sec_uid。"""

    @abc.abstractmethod
    def list_latest(self, sec_uid: str, n: int) -> Result[list[Video], str]:
        """获取该博主最新 n 个视频的元数据（含临时下载地址）。"""

    @abc.abstractmethod
    def get_download_url(self, aweme_id: str) -> Result[str, str]:
        """获取单个视频的（无水印）下载地址，下载前刷新用。"""

    @abc.abstractmethod
    def fetch_profile(self, sec_uid: str) -> Result[dict, str]:
        """拉取博主资料，返回 {nickname, aweme_count}，用于 sec_uid 事后校验。"""


class FakeDouyinClient(DouyinClient):
    """内存假实现：构造期注入返回值，供单测与离线联调。"""

    def __init__(self, sec_uid: str = "fake_sec", videos: list[Video] | None = None):
        self._sec_uid = sec_uid
        self._videos = videos or []

    def resolve_sec_uid(self, name_or_url: str) -> Result[str, str]:
        return Ok(self._sec_uid)

    def list_latest(self, sec_uid: str, n: int) -> Result[list[Video], str]:
        return Ok(self._videos[:n])

    def get_download_url(self, aweme_id: str) -> Result[str, str]:
        for v in self._videos:
            if v.aweme_id == aweme_id and v.download_url:
                return Ok(v.download_url)
        return Err(f"未找到视频 {aweme_id} 的下载地址")

    def fetch_profile(self, sec_uid: str) -> Result[dict, str]:
        return Ok({"nickname": "测试博主", "aweme_count": 42})
