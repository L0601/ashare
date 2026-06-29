"""媒体处理：从视频抽取音轨。"""

from .audio import build_ffmpeg_cmd, extract_audio, probe_duration

__all__ = ["build_ffmpeg_cmd", "extract_audio", "probe_duration"]
