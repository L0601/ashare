"""SenseVoice sherpa-onnx 后端单测，不加载真实模型。"""

import sys
from types import ModuleType, SimpleNamespace

import numpy as np

from ashare.asr.sensevoice import (
    SenseVoiceTranscriber,
    _join_texts,
    _model_files,
    _normalize_language,
    _split_audio,
)


class FakeStream:
    def __init__(self):
        self.result = SimpleNamespace(text=" 看好半导体 ")
        self.accepted = None

    def accept_waveform(self, sample_rate, audio):
        self.accepted = (sample_rate, audio)


class FakeRecognizer:
    def __init__(self):
        self.streams = []

    def create_stream(self):
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def decode_stream(self, stream):
        return None


def test_model_files_accepts_directory_and_onnx_path(tmp_path):
    model, tokens = _model_files(tmp_path)
    assert model == tmp_path / "model.int8.onnx"
    assert tokens == tmp_path / "tokens.txt"

    direct, direct_tokens = _model_files(model)
    assert direct == model
    assert direct_tokens == tokens


def test_language_names_are_backward_compatible():
    assert _normalize_language("Chinese") == "zh"
    assert _normalize_language("zh") == "zh"
    assert _normalize_language("") == "auto"


def test_split_audio_uses_short_chunks_and_preserves_samples():
    sample_rate = 10
    audio = np.ones(75 * sample_rate, dtype=np.float32)
    audio[28 * sample_rate : 32 * sample_rate] = 0

    chunks = _split_audio(audio, sample_rate, chunk_seconds=30)

    assert len(chunks) == 3
    assert sum(map(len, chunks)) == len(audio)
    assert max(map(len, chunks)) <= 33 * sample_rate
    assert 28 * sample_rate <= len(chunks[0]) <= 32 * sample_rate


def test_join_texts_only_inserts_space_between_english_words():
    assert _join_texts(["第一句。", "第二句。"]) == "第一句。第二句。"
    assert _join_texts(["hello", "world"]) == "hello world"


def test_transcriber_reuses_and_reloads_recognizer(tmp_path):
    class CountingTranscriber(SenseVoiceTranscriber):
        load_count = 0

        def _load_recognizer(self):
            self.load_count += 1
            return FakeRecognizer()

        @staticmethod
        def _read_audio(path):
            return [0.0, 0.1], 16000

    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"WAV")
    transcriber = CountingTranscriber(str(tmp_path))

    assert transcriber.transcribe(wav).value.text == "看好半导体"
    assert transcriber.transcribe(wav).is_ok()
    assert transcriber.load_count == 1
    transcriber.release()
    assert transcriber.transcribe(wav).is_ok()
    assert transcriber.load_count == 2


def test_long_audio_is_decoded_as_multiple_chunks(tmp_path):
    class ChunkingTranscriber(SenseVoiceTranscriber):
        def _load_recognizer(self):
            return FakeRecognizer()

        @staticmethod
        def _read_audio(path):
            return np.ones(65 * 10, dtype=np.float32), 10

    wav = tmp_path / "long.wav"
    wav.write_bytes(b"WAV")
    transcriber = ChunkingTranscriber(str(tmp_path), chunk_seconds=30)

    result = transcriber.transcribe(wav)

    assert result.is_ok()
    assert len(transcriber._recognizer.streams) == 3
    assert result.value.text == "看好半导体" * 3


def test_load_recognizer_uses_cpu_int8_settings(tmp_path, monkeypatch):
    model = tmp_path / "model.int8.onnx"
    tokens = tmp_path / "tokens.txt"
    model.write_bytes(b"model")
    tokens.write_text("a 0", encoding="utf-8")
    seen = {}
    module = ModuleType("sherpa_onnx")
    module.OfflineRecognizer = SimpleNamespace(
        from_sense_voice=lambda **kwargs: seen.update(kwargs) or FakeRecognizer(),
    )
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)

    transcriber = SenseVoiceTranscriber(
        str(tmp_path),
        language="Chinese",
        num_threads=4,
        use_itn=True,
    )
    transcriber._load_recognizer()

    assert seen["model"] == str(model)
    assert seen["tokens"] == str(tokens)
    assert seen["provider"] == "cpu"
    assert seen["num_threads"] == 4
    assert seen["language"] == "zh"
    assert seen["use_itn"] is True
