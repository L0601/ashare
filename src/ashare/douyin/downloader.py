"""视频下载：httpx 流式下载到本地文件，带重试与限速。

sleep 通过参数注入，便于测试时传入假实现，不依赖真实时钟。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import httpx

from ..result import Err, Ok, Result


def download_video(
    url: str,
    dest: str | Path,
    *,
    user_agent: str,
    cookie: str = "",
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> Result[Path, str]:
    """下载 url 到 dest；失败按指数退避重试。返回最终文件路径或错误。"""
    if not url:
        return Err("下载地址为空")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": user_agent, "Referer": "https://www.douyin.com/"}
    if cookie:
        headers["Cookie"] = cookie

    last_err = ""
    for attempt in range(retries):
        try:
            _stream_to_file(url, dest, headers)
            if dest.stat().st_size > 0:
                return Ok(dest)
            last_err = "下载内容为空"
        except Exception as e:
            last_err = str(e)
        sleep(2 ** attempt)  # 1s, 2s, 4s ...
    return Err(f"下载失败（重试 {retries} 次）：{last_err}")


def _stream_to_file(url: str, dest: Path, headers: dict) -> None:
    """流式写入，避免大文件占满内存。"""
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1 << 16):
                f.write(chunk)
