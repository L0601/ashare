"""Qwen3-ASR-1.7B 本地转写实现。

两种后端：
- mlx：Apple Silicon 上最优（Metal 加速），走 mlx-audio。
- transformers：官方通用路线，CPU/MPS 皆可，作兜底。

模型在每轮首次转写时按需加载，轮次结束后可释放。外部失败统一转 Result.Err（可恢复）。

注意：Qwen3-ASR 较新，不同版本的加载/推理入口可能微调。本实现把加载与推理
集中在本文件，若模型卡接口有变，只需改这里；其余链路通过 Transcriber 接口不受影响。
首次运行会自动从 HuggingFace/ModelScope 下载权重（数 GB）。
"""

from __future__ import annotations

import gc
import logging
from pathlib import Path

from .base import Transcriber, TranscriptResult
from ..result import Err, Ok, Result

logger = logging.getLogger("ashare.asr")
# mlx-audio 默认 20 分钟一段，长视频会产生过大的 Metal 推理峰值。
MLX_CHUNK_SECONDS = 3 * 60


def _result_text(result) -> str:
    """从 ASR 返回值中提取正文；空文本保持为空，不回退到对象 repr。"""
    if result is None:
        return ""
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        value = result.get("text", "")
    else:
        value = getattr(result, "text", "")
    return str(value).strip() if value is not None else ""


class Qwen3Transcriber(Transcriber):
    def __init__(self, model: str, backend: str = "mlx", language: str = "Chinese"):
        self._model_id = model
        self._backend = backend
        self._language = language
        self._engine = None  # 懒加载后缓存

    def transcribe(self, wav_path: str | Path) -> Result[TranscriptResult, str]:
        wav_path = Path(wav_path)
        if not wav_path.exists():
            return Err(f"音频文件不存在：{wav_path}")
        try:
            text = self._ensure_engine()(str(wav_path))
            return Ok(TranscriptResult(
                text=text.strip(), engine=f"qwen3-asr/{self._backend}",
            ))
        except Exception as e:
            return Err(f"转写失败（{self._backend}）：{e}")

    def _ensure_engine(self):
        """返回一个 (wav_path)->text 的可调用，首次调用时加载模型。"""
        if self._engine is None:
            if self._backend == "mlx":
                self._engine = self._load_mlx()
            else:
                self._engine = self._load_transformers()
        return self._engine

    def release(self) -> None:
        """断开模型引用并清理后端缓存，使下一次转写重新加载。"""
        if self._engine is None:
            return
        self._engine = None
        gc.collect()
        _clear_backend_cache(self._backend)

    def _load_mlx(self):
        """MLX 后端：mlx-audio 的 load_model + generate_transcription（按官方模型卡）。"""
        import os
        import tempfile

        from mlx_audio.stt.utils import load_model  # type: ignore
        from mlx_audio.stt.generate import generate_transcription  # type: ignore

        model = load_model(self._model_id)  # 接受本地路径或 HF 名；本轮只加载一次
        tmp_out = os.path.join(tempfile.gettempdir(), "ashare_asr_out")

        def run(wav: str) -> str:
            result = generate_transcription(
                model=model, audio=wav, output_path=tmp_out, format="txt",
                verbose=False, chunk_duration=MLX_CHUNK_SECONDS,
            )
            return _result_text(result)

        return run

    def _load_transformers(self):
        """transformers 后端：自动选择 mps/cpu。"""
        import torch  # type: ignore
        from transformers import pipeline  # type: ignore

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        pipe = pipeline(
            task="automatic-speech-recognition",
            model=self._model_id,
            trust_remote_code=True,
            torch_dtype=torch.float16 if device == "mps" else torch.float32,
            device=device,
        )
        lang = self._language

        def run(wav: str) -> str:
            out = pipe(wav, generate_kwargs={"language": lang})
            return _result_text(out)

        return run


def _clear_backend_cache(backend: str) -> None:
    """尽力释放推理后端缓存；清理失败不影响业务结果。"""
    try:
        if backend == "mlx":
            import mlx.core as mx  # type: ignore

            mx.clear_cache()
        elif backend == "transformers":
            import torch  # type: ignore

            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
    except Exception:
        logger.warning("ASR 后端缓存清理失败", exc_info=True)
