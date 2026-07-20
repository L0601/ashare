"""ASR 抽象接口、结果类型、Fake 实现与工厂。

转写器抽象成接口，pipeline 只依赖接口；引擎由配置选择。
算法与 IO 分离：上层只给 wav 路径，拿回文本，不关心模型细节。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path

from ..config import ASRConfig
from ..result import Ok, Result


@dataclass
class TranscriptResult:
    text: str
    engine: str
    audio_seconds: float = 0.0


class Transcriber(abc.ABC):
    """语音转写统一接口。"""

    @abc.abstractmethod
    def transcribe(self, wav_path: str | Path) -> Result[TranscriptResult, str]:
        """把 16k 单声道 wav 转成文字。"""

    def release(self) -> None:
        """释放模型等重量级资源；无状态实现无需处理。"""


class FakeTranscriber(Transcriber):
    """测试/离线用：返回预设文本。"""

    def __init__(self, text: str = "这是一段假的转写文本，用于测试。"):
        self._text = text

    def transcribe(self, wav_path: str | Path) -> Result[TranscriptResult, str]:
        return Ok(TranscriptResult(text=self._text, engine="fake"))


def make_transcriber(cfg: ASRConfig) -> Transcriber:
    """按配置创建转写器。延迟导入重量级后端，避免无谓依赖。"""
    engine = cfg.engine.lower()
    if engine == "fake":
        return FakeTranscriber()
    if engine == "sensevoice":
        from .sensevoice import SenseVoiceTranscriber

        return SenseVoiceTranscriber(
            model=cfg.model,
            language=cfg.language,
            num_threads=cfg.num_threads,
            chunk_seconds=cfg.chunk_seconds,
            use_itn=cfg.use_itn,
        )
    if engine in ("mlx", "transformers"):
        from .qwen3 import Qwen3Transcriber

        return Qwen3Transcriber(model=cfg.model, backend=engine, language=cfg.language)
    raise ValueError(f"未知 ASR 引擎：{cfg.engine}")
