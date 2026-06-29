"""ASR 接口单测：Fake 转写 + 工厂选择（不触碰真实模型）。"""

import pytest

from ashare.asr.base import FakeTranscriber, make_transcriber
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
