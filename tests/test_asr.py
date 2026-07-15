"""ASR 接口单测：Fake 转写 + 工厂选择（不触碰真实模型）。"""

import sys
from types import ModuleType

import pytest

from ashare.asr.base import FakeTranscriber, make_transcriber
from ashare.asr import qwen3
from ashare.asr.qwen3 import Qwen3Transcriber, _result_text
from ashare.asr.sensevoice import SenseVoiceTranscriber
from ashare.config import ASRConfig
from ashare.result import Ok


def test_fake_transcriber():
    r = FakeTranscriber("看好半导体").transcribe("whatever.wav")
    assert isinstance(r, Ok)
    assert r.value.text == "看好半导体"
    assert r.value.engine == "fake"


def test_make_transcriber_fake():
    t = make_transcriber(ASRConfig(engine="fake"))
    assert isinstance(t, FakeTranscriber)


def test_make_transcriber_sensevoice():
    t = make_transcriber(ASRConfig(engine="sensevoice"))
    assert isinstance(t, SenseVoiceTranscriber)


def test_make_transcriber_unknown():
    with pytest.raises(ValueError):
        make_transcriber(ASRConfig(engine="bogus"))


def test_qwen3_result_text_prefers_empty_text_field():
    class DummyResult:
        text = ""

        def __str__(self):
            return "STTOutput(text='', segments=[...])"

    assert _result_text(DummyResult()) == ""


def test_qwen3_release_makes_next_transcription_reload_model(tmp_path, monkeypatch):
    class CountingTranscriber(Qwen3Transcriber):
        def __init__(self):
            super().__init__(model="fake-model", backend="mlx")
            self.load_count = 0

        def _load_mlx(self):
            self.load_count += 1
            return lambda wav: "转写结果"

    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"WAV")
    transcriber = CountingTranscriber()
    cleared_backends = []
    monkeypatch.setattr(
        qwen3, "_clear_backend_cache", cleared_backends.append, raising=False,
    )

    assert transcriber.transcribe(wav).is_ok()
    assert transcriber.transcribe(wav).is_ok()
    assert transcriber.load_count == 1

    transcriber.release()

    assert cleared_backends == ["mlx"]
    assert transcriber.transcribe(wav).is_ok()
    assert transcriber.load_count == 2


def test_mlx_transcription_limits_each_chunk_to_three_minutes(monkeypatch):
    seen = {}
    mlx_audio = ModuleType("mlx_audio")
    mlx_audio.__path__ = []
    stt = ModuleType("mlx_audio.stt")
    stt.__path__ = []
    utils = ModuleType("mlx_audio.stt.utils")
    generate = ModuleType("mlx_audio.stt.generate")
    utils.load_model = lambda model_id: object()

    def fake_generate_transcription(**kwargs):
        seen.update(kwargs)
        return {"text": "转写结果"}

    generate.generate_transcription = fake_generate_transcription
    monkeypatch.setitem(sys.modules, "mlx_audio", mlx_audio)
    monkeypatch.setitem(sys.modules, "mlx_audio.stt", stt)
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.utils", utils)
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.generate", generate)

    engine = Qwen3Transcriber(model="fake-model", backend="mlx")._load_mlx()
    result = engine("long-audio.wav")

    assert result == "转写结果"
    assert seen["chunk_duration"] == 180
