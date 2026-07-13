"""ASR 接口单测：Fake 转写 + 工厂选择（不触碰真实模型）。"""

import pytest

from ashare.asr.base import FakeTranscriber, make_transcriber
from ashare.asr import qwen3
from ashare.asr.qwen3 import Qwen3Transcriber, _result_text
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
