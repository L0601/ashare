"""用 ffmpeg 从视频抽取音轨为 16k 单声道 wav（ASR 标准输入）。

命令构造（纯函数）与执行（subprocess）分离：命令构造可单测，
执行通过 runner 注入，测试时可传假实现。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

from ..result import Err, Ok, Result

# Qwen3-ASR 期望 16kHz 采样率；单声道足够语音识别且更省资源
TARGET_SAMPLE_RATE = 16000


def build_ffmpeg_cmd(src: str | Path, dst: str | Path) -> list[str]:
    """构造抽音轨命令：去视频流、单声道、16k、wav。纯函数。"""
    return [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vn",                          # 丢弃视频流
        "-ac", "1",                     # 单声道
        "-ar", str(TARGET_SAMPLE_RATE),  # 16kHz
        "-f", "wav",
        str(dst),
    ]


def extract_audio(
    src: str | Path,
    dst: str | Path,
    runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
) -> Result[Path, str]:
    """执行抽音轨。runner 缺省走真实 subprocess，可注入假实现。"""
    src, dst = Path(src), Path(dst)
    if not src.exists():
        return Err(f"源视频不存在：{src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    run = runner or _default_runner
    proc = run(build_ffmpeg_cmd(src, dst))
    if proc.returncode != 0:
        return Err(f"ffmpeg 失败（code={proc.returncode}）：{(proc.stderr or '')[-500:]}")
    if not dst.exists() or dst.stat().st_size == 0:
        return Err("ffmpeg 未产出有效音频")
    return Ok(dst)


def probe_duration(src: str | Path, runner: Callable | None = None) -> float:
    """用 ffprobe 取媒体时长（秒）；失败返回 0。"""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", str(src),
    ]
    run = runner or _default_runner
    try:
        proc = run(cmd)
        data = json.loads(proc.stdout or "{}")
        return float(data.get("format", {}).get("duration", 0) or 0)
    except Exception:
        return 0.0


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)
