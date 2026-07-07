"""音轨抽取单测：命令构造（纯函数）+ 执行分支（runner 注入）。"""

import subprocess

from ashare.media.audio import _default_runner, build_ffmpeg_cmd, extract_audio
from ashare.result import Err, Ok


def test_build_ffmpeg_cmd():
    cmd = build_ffmpeg_cmd("a.mp4", "b.wav")
    assert cmd[0] == "ffmpeg"
    assert "-nostdin" in cmd                 # 后台运行时不读终端输入
    assert "-vn" in cmd                      # 去视频
    assert cmd[cmd.index("-ar") + 1] == "16000"  # 16k
    assert cmd[cmd.index("-ac") + 1] == "1"      # 单声道
    assert cmd[-1] == "b.wav"


def test_default_runner_detaches_stdin(monkeypatch):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _default_runner(["ffmpeg", "-version"])

    assert calls["kwargs"]["stdin"] == subprocess.DEVNULL


def _ok_proc(*a, **k):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_extract_audio_source_missing(tmp_path):
    r = extract_audio(tmp_path / "nope.mp4", tmp_path / "o.wav", runner=_ok_proc)
    assert isinstance(r, Err)


def test_extract_audio_success(tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    dst = tmp_path / "o.wav"

    def runner(cmd):
        dst.write_bytes(b"WAVDATA")  # 模拟 ffmpeg 产出
        return _ok_proc()

    r = extract_audio(src, dst, runner=runner)
    assert isinstance(r, Ok)
    assert r.value == dst


def test_extract_audio_ffmpeg_failure(tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")

    def runner(cmd):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")

    r = extract_audio(src, tmp_path / "o.wav", runner=runner)
    assert isinstance(r, Err)
    assert "boom" in r.error
