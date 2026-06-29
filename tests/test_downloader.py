"""下载器单测：成功、空地址、重试退避（sleep 注入，不依赖真实时钟）。"""

from ashare.douyin import downloader
from ashare.result import Err, Ok


def test_download_empty_url():
    r = downloader.download_video("", "/tmp/x.mp4", user_agent="ua")
    assert isinstance(r, Err)


def test_download_success(tmp_path, monkeypatch):
    dest = tmp_path / "v.mp4"

    def fake_stream(url, d, headers):
        d.write_bytes(b"FAKEVIDEO")

    monkeypatch.setattr(downloader, "_stream_to_file", fake_stream)
    r = downloader.download_video("http://x/v.mp4", dest, user_agent="ua", cookie="ck")
    assert isinstance(r, Ok)
    assert dest.read_bytes() == b"FAKEVIDEO"


def test_download_retries_then_fails(tmp_path, monkeypatch):
    calls = {"stream": 0, "sleeps": []}

    def fail_stream(url, d, headers):
        calls["stream"] += 1
        raise RuntimeError("网络错误")

    monkeypatch.setattr(downloader, "_stream_to_file", fail_stream)
    r = downloader.download_video(
        "http://x/v.mp4", tmp_path / "v.mp4", user_agent="ua",
        retries=3, sleep=lambda s: calls["sleeps"].append(s),
    )
    assert isinstance(r, Err)
    assert calls["stream"] == 3
    assert calls["sleeps"] == [1, 2, 4]  # 指数退避


def test_download_recovers_on_second_attempt(tmp_path, monkeypatch):
    state = {"n": 0}

    def flaky_stream(url, d, headers):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("第一次失败")
        d.write_bytes(b"OK")

    monkeypatch.setattr(downloader, "_stream_to_file", flaky_stream)
    r = downloader.download_video(
        "http://x/v.mp4", tmp_path / "v.mp4", user_agent="ua",
        sleep=lambda s: None,
    )
    assert isinstance(r, Ok)
    assert state["n"] == 2
