"""语音转写层：SenseVoice/Qwen3-ASR 实现 + 测试用 Fake。"""

from .base import FakeTranscriber, TranscriptResult, Transcriber, make_transcriber

__all__ = ["Transcriber", "TranscriptResult", "FakeTranscriber", "make_transcriber"]
