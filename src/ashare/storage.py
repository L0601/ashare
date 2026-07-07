"""本地中间文件路径规则。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Video

_BJ_TZ = timezone(timedelta(hours=8))


def video_dir_name(video: Video | None, aweme_id: str) -> str:
    """目录名：北京时间发布日期 + 视频 id。无发布时间时用 unknown。"""
    if video and video.publish_time:
        day = datetime.fromtimestamp(video.publish_time, _BJ_TZ).strftime("%Y-%m-%d")
    else:
        day = "unknown"
    return f"{day}_{aweme_id}"


def video_dir(base_dir: str | Path, video: Video | None, aweme_id: str) -> Path:
    return Path(base_dir) / video_dir_name(video, aweme_id)
