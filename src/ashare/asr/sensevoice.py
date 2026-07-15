"""SenseVoice Small INT8 的 sherpa-onnx 本地转写实现。"""

from __future__ import annotations

import gc
from pathlib import Path

import numpy as np

from .base import Transcriber, TranscriptResult
from ..result import Err, Ok, Result

MODEL_FILENAME = "model.int8.onnx"
TOKENS_FILENAME = "tokens.txt"
SILENCE_SEARCH_SECONDS = 3.0
SILENCE_FRAME_SECONDS = 0.2
LANGUAGE_ALIASES = {
    "chinese": "zh",
    "english": "en",
    "japanese": "ja",
    "korean": "ko",
    "cantonese": "yue",
}


def _model_files(model: str | Path) -> tuple[Path, Path]:
    """支持传模型目录，也支持直接传 model.int8.onnx。"""
    path = Path(model).expanduser()
    root = path.parent if path.suffix == ".onnx" else path
    model_path = path if path.suffix == ".onnx" else root / MODEL_FILENAME
    return model_path, root / TOKENS_FILENAME


def _normalize_language(language: str) -> str:
    value = language.strip().lower()
    return LANGUAGE_ALIASES.get(value, value or "auto")


def _quiet_split(audio, start: int, target: int, sample_rate: int) -> int:
    """在目标位置附近选择能量最低的短帧中点，降低截断语句的概率。"""
    radius = int(sample_rate * SILENCE_SEARCH_SECONDS)
    left = max(start + sample_rate, target - radius)
    right = min(len(audio), target + radius)
    frame = max(1, int(sample_rate * SILENCE_FRAME_SECONDS))
    frame_count = (right - left) // frame
    if frame_count <= 0:
        return target
    window = np.asarray(audio[left : left + frame_count * frame])
    energies = np.mean(np.abs(window.reshape(frame_count, frame)), axis=1)
    return left + int(np.argmin(energies)) * frame + frame // 2


def _split_audio(audio, sample_rate: int, chunk_seconds: int) -> list:
    """把长音频切成约定长度，并让边界尽量落在低能量位置。"""
    limit = max(1, int(sample_rate * chunk_seconds))
    if len(audio) <= limit:
        return [audio]
    chunks, start = [], 0
    while len(audio) - start > limit:
        target = start + limit
        end = _quiet_split(audio, start, target, sample_rate)
        chunks.append(audio[start:end])
        start = end
    chunks.append(audio[start:])
    return chunks


def _join_texts(texts: list[str]) -> str:
    """中文直接连接；连续英文单词之间补一个空格。"""
    merged = ""
    for text in filter(None, (item.strip() for item in texts)):
        needs_space = (
            merged
            and merged[-1].isascii()
            and merged[-1].isalnum()
            and text[0].isascii()
            and text[0].isalnum()
        )
        merged += (" " if needs_space else "") + text
    return merged


class SenseVoiceTranscriber(Transcriber):
    """CPU INT8 离线转写器；模型首次使用时加载并复用。"""

    def __init__(
        self,
        model: str,
        language: str = "zh",
        num_threads: int = 4,
        chunk_seconds: int = 30,
        use_itn: bool = True,
    ):
        self._model = model
        self._language = _normalize_language(language)
        self._num_threads = num_threads
        self._chunk_seconds = chunk_seconds
        self._use_itn = use_itn
        self._recognizer = None

    def transcribe(self, wav_path: str | Path) -> Result[TranscriptResult, str]:
        path = Path(wav_path)
        if not path.exists():
            return Err(f"音频文件不存在：{path}")
        try:
            audio, sample_rate = self._read_audio(path)
            recognizer = self._ensure_recognizer()
            chunks = _split_audio(audio, sample_rate, self._chunk_seconds)
            text = self._decode_chunks(recognizer, chunks, sample_rate)
            return Ok(
                TranscriptResult(
                    text=text,
                    engine="sensevoice/sherpa-onnx-int8",
                )
            )
        except Exception as exc:
            return Err(f"转写失败（sensevoice/cpu-int8）：{exc}")

    @staticmethod
    def _decode_chunks(recognizer, chunks: list, sample_rate: int) -> str:
        texts = []
        for audio in chunks:
            stream = recognizer.create_stream()
            stream.accept_waveform(sample_rate, audio)
            recognizer.decode_stream(stream)
            texts.append(stream.result.text)
        return _join_texts(texts)

    def release(self) -> None:
        self._recognizer = None
        gc.collect()

    def _ensure_recognizer(self):
        if self._recognizer is None:
            self._recognizer = self._load_recognizer()
        return self._recognizer

    def _load_recognizer(self):
        import sherpa_onnx  # type: ignore

        model, tokens = _model_files(self._model)
        missing = [str(path) for path in (model, tokens) if not path.is_file()]
        if missing:
            raise FileNotFoundError("缺少 SenseVoice 模型文件：" + "、".join(missing))
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model),
            tokens=str(tokens),
            num_threads=self._num_threads,
            provider="cpu",
            language=self._language,
            use_itn=self._use_itn,
        )

    @staticmethod
    def _read_audio(path: Path):
        import soundfile as sf  # type: ignore

        audio, sample_rate = sf.read(
            str(path),
            dtype="float32",
            always_2d=True,
        )
        return audio[:, 0], sample_rate
