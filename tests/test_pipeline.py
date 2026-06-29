"""流水线状态机单测：全链路成功、阶段失败即停、幂等续跑、只处理未完成。

全部用 fake 依赖，不联网、不碰真实模型/ffmpeg。
"""

from types import SimpleNamespace

import pytest

from ashare.asr.base import FakeTranscriber
from ashare.config import (
    AppConfig,
    ASRConfig,
    DouyinConfig,
    LLMConfig,
    StorageConfig,
)
from ashare.db import Database
from ashare.douyin.base import FakeDouyinClient
from ashare.llm.extractor import LLMExtractor
from ashare.models import Stage, Status, Video
from ashare.result import Err, Ok

GOOD_JSON = '{"title":"看多新能源龙头","summary":"看多新能源","sectors":["新能源"],"opinions":[{"target_name":"宁德时代","target_code":"300750","target_type":"stock","stance":"看多","confidence":0.8,"logic":"龙头","quote":"看好"}]}'


def _fake_llm_client(content=GOOD_JSON):
    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    return SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=lambda **kw: resp)))


def _cfg(tmp_path):
    return AppConfig(
        douyin=DouyinConfig(cookie="ck", author_sec_uid="sec1", latest_count=10),
        llm=LLMConfig(base_url="http://x", api_key="k", model="m"),
        asr=ASRConfig(engine="fake"),
        storage=StorageConfig(data_dir=str(tmp_path / "data")),
    )


def _videos():
    return [
        Video("v1", "sec1", "标题1", "https://d/v1", publish_time=200, download_url="http://x/1.mp4"),
        Video("v2", "sec1", "标题2", "https://d/v2", publish_time=100, download_url="http://x/2.mp4"),
    ]


def _build(tmp_path, *, download_fn=None, audio_fn=None, transcriber=None):
    from ashare.pipeline.runner import Pipeline

    cfg = _cfg(tmp_path)
    db = Database(":memory:", clock=lambda: 1000)

    def ok_download(url, dest, **kw):
        from pathlib import Path
        Path(dest).write_bytes(b"VIDEO")
        return Ok(Path(dest))

    def ok_audio(src, dst, **kw):
        from pathlib import Path
        Path(dst).write_bytes(b"WAV")
        return Ok(Path(dst))

    pipe = Pipeline(
        cfg=cfg, db=db,
        client=FakeDouyinClient(sec_uid="sec1", videos=_videos()),
        transcriber=transcriber or FakeTranscriber("我看好新能源板块"),
        extractor=LLMExtractor(cfg.llm, client=_fake_llm_client()),
        download_fn=download_fn or ok_download,
        extract_audio_fn=audio_fn or ok_audio,
        probe_fn=lambda p: 42.0,
        pace=lambda: None,
    )
    return pipe, db


def test_discover_inserts_new_videos(tmp_path):
    pipe, db = _build(tmp_path)
    res = pipe.discover()
    assert isinstance(res, Ok)
    assert len(res.value) == 2
    assert db.known_aweme_ids() == {"v1", "v2"}
    assert db.get_stage_status("v1", Stage.DISCOVER) == Status.SUCCESS


def test_discover_no_dup_second_time(tmp_path):
    pipe, db = _build(tmp_path)
    pipe.discover()
    res2 = pipe.discover()
    assert len(res2.value) == 0  # 第二次没有新视频


def test_process_video_full_success(tmp_path):
    pipe, db = _build(tmp_path)
    pipe.discover()
    ok = pipe.process_video(db.get_video("v1"))
    assert ok is True
    assert db.get_video("v1").overall_status == Status.SUCCESS
    for st in Stage.ordered():
        assert db.get_stage_status("v1", st) == Status.SUCCESS
    # 转写与分析都已落库
    assert db.get_transcript("v1").audio_seconds == 42.0
    analysis = db.get_analysis("v1")
    assert analysis.summary == "看多新能源"
    assert analysis.opinions[0].target_name == "宁德时代"
    # 中间文件落盘
    base = pipe._dir("v1")
    assert (base / "video.mp4").exists()
    assert (base / "audio.wav").exists()
    assert (base / "transcript.txt").read_text(encoding="utf-8") == "我看好新能源板块"


def test_extract_backfills_empty_title(tmp_path):
    """视频无原生文案标题时，抽取后用 LLM 生成的标题回填 videos.title。"""
    from ashare.pipeline.runner import Pipeline

    cfg = _cfg(tmp_path)
    db = Database(":memory:", clock=lambda: 1000)
    empty = Video("e1", "sec1", "", "https://d/e1", publish_time=300, download_url="http://x/1.mp4")
    db.upsert_video(empty)

    def ok_dl(url, dest, **kw):
        from pathlib import Path
        Path(dest).write_bytes(b"V"); return Ok(Path(dest))

    def ok_au(src, dst, **kw):
        from pathlib import Path
        Path(dst).write_bytes(b"W"); return Ok(Path(dst))

    pipe = Pipeline(
        cfg=cfg, db=db, client=FakeDouyinClient(videos=[empty]),
        transcriber=FakeTranscriber("看好新能源"),
        extractor=LLMExtractor(cfg.llm, client=_fake_llm_client()),
        download_fn=ok_dl, extract_audio_fn=ok_au, probe_fn=lambda p: 1.0, pace=lambda: None,
    )
    assert db.get_video("e1").title == ""
    pipe.process_video(db.get_video("e1"))
    assert db.get_video("e1").title == "看多新能源龙头"   # 已回填


def test_existing_title_not_overwritten(tmp_path):
    """已有原生标题的视频，不被 LLM 标题覆盖。"""
    pipe, db = _build(tmp_path)
    pipe.discover()
    pipe.process_video(db.get_video("v1"))
    assert db.get_video("v1").title == "标题1"   # 保留原文案


def test_notify_stage_pushes_on_success(tmp_path):
    """视频跑完，NOTIFY 阶段用 FakeNotifier 推送一条含标题/作者/观点的消息。"""
    from ashare.notify.base import FakeNotifier
    from ashare.pipeline.runner import Pipeline

    cfg = _cfg(tmp_path)
    db = Database(":memory:", clock=lambda: 1000)
    fake_notifier = FakeNotifier()
    videos = [Video("v1", "sec1", "看好新能源", "https://d/v1", publish_time=200,
                    author_name="创业魔法师", download_url="http://x/1.mp4")]

    def ok_dl(url, dest, **kw):
        from pathlib import Path
        Path(dest).write_bytes(b"V"); return Ok(Path(dest))

    def ok_au(src, dst, **kw):
        from pathlib import Path
        Path(dst).write_bytes(b"W"); return Ok(Path(dst))

    pipe = Pipeline(
        cfg=cfg, db=db, client=FakeDouyinClient(sec_uid="sec1", videos=videos),
        transcriber=FakeTranscriber("我看好新能源"),
        extractor=LLMExtractor(cfg.llm, client=_fake_llm_client()),
        notifier=fake_notifier,
        download_fn=ok_dl, extract_audio_fn=ok_au, probe_fn=lambda p: 1.0, pace=lambda: None,
    )
    pipe.discover()
    assert pipe.process_video(db.get_video("v1")) is True
    assert db.get_stage_status("v1", Stage.NOTIFY) == Status.SUCCESS
    assert len(fake_notifier.sent) == 1
    msg = fake_notifier.sent[0]
    assert "创业魔法师" in msg and "宁德时代" in msg and "我看好新能源" in msg


def test_notify_failure_marks_video_failed(tmp_path):
    """推送失败 → NOTIFY 阶段 failed、视频 failed，下次可重试（幂等不重复发已成功阶段）。"""
    from ashare.notify.base import Notifier
    from ashare.pipeline.runner import Pipeline
    from ashare.result import Err as _Err

    class FailNotifier(Notifier):
        def send(self, text):
            return _Err("网络错误")

    cfg = _cfg(tmp_path)
    db = Database(":memory:", clock=lambda: 1000)
    videos = [Video("v1", "sec1", "t", "https://d/v1", publish_time=200, download_url="http://x/1.mp4")]

    def ok_dl(url, dest, **kw):
        from pathlib import Path
        Path(dest).write_bytes(b"V"); return Ok(Path(dest))

    def ok_au(src, dst, **kw):
        from pathlib import Path
        Path(dst).write_bytes(b"W"); return Ok(Path(dst))

    pipe = Pipeline(
        cfg=cfg, db=db, client=FakeDouyinClient(sec_uid="sec1", videos=videos),
        transcriber=FakeTranscriber("x"),
        extractor=LLMExtractor(cfg.llm, client=_fake_llm_client()),
        notifier=FailNotifier(),
        download_fn=ok_dl, extract_audio_fn=ok_au, probe_fn=lambda p: 1.0, pace=lambda: None,
    )
    pipe.discover()
    assert pipe.process_video(db.get_video("v1")) is False
    assert db.get_stage_status("v1", Stage.EXTRACT) == Status.SUCCESS   # 抽取已完成
    assert db.get_stage_status("v1", Stage.NOTIFY) == Status.FAILED
    assert db.get_video("v1").overall_status == Status.FAILED


def test_stage_failure_stops_chain(tmp_path):
    """转写失败：transcribe 标 failed，后续 extract 不执行，视频整体 failed。"""
    class FailTranscriber(FakeTranscriber):
        def transcribe(self, wav_path):
            return Err("模型崩了")

    pipe, db = _build(tmp_path, transcriber=FailTranscriber())
    pipe.discover()
    ok = pipe.process_video(db.get_video("v1"))
    assert ok is False
    assert db.get_video("v1").overall_status == Status.FAILED
    assert db.get_stage_status("v1", Stage.DOWNLOAD) == Status.SUCCESS
    assert db.get_stage_status("v1", Stage.EXTRACT_AUDIO) == Status.SUCCESS
    assert db.get_stage_status("v1", Stage.TRANSCRIBE) == Status.FAILED
    assert db.get_stage_status("v1", Stage.EXTRACT) == Status.PENDING


def test_resume_skips_completed_stages(tmp_path):
    """续跑：已成功的阶段不应再次执行（下载函数不应被再次调用）。"""
    calls = {"download": 0}

    def counting_download(url, dest, **kw):
        from pathlib import Path
        calls["download"] += 1
        Path(dest).write_bytes(b"V")
        return Ok(Path(dest))

    pipe, db = _build(tmp_path, download_fn=counting_download)
    pipe.discover()
    pipe.process_video(db.get_video("v1"))
    assert calls["download"] == 1
    # 再次处理同一视频：所有阶段已成功，下载不再触发
    pipe.process_video(db.get_video("v1"))
    assert calls["download"] == 1


def test_run_once_processes_only_unfinished(tmp_path):
    pipe, db = _build(tmp_path)
    stats = pipe.run_once()
    assert stats["new"] == 2
    assert stats["processed"] == 2
    # 第二轮：无新视频，且都已成功，处理数为 0
    stats2 = pipe.run_once()
    assert stats2["new"] == 0
    assert stats2["processed"] == 0


def test_run_once_records_poll(tmp_path):
    pipe, db = _build(tmp_path)
    pipe.run_once()
    row = db.conn.execute("SELECT * FROM poll_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "success"
    assert row["new_count"] == 2


def test_discover_failure_marks_poll_failed(tmp_path):
    from ashare.pipeline.runner import Pipeline

    cfg = _cfg(tmp_path)
    db = Database(":memory:", clock=lambda: 1000)

    class FailClient(FakeDouyinClient):
        def list_latest(self, sec_uid, n):
            return Err("风控拦截")

    pipe = Pipeline(
        cfg=cfg, db=db, client=FailClient(),
        transcriber=FakeTranscriber(),
        extractor=LLMExtractor(cfg.llm, client=_fake_llm_client()),
        pace=lambda: None,
    )
    stats = pipe.run_once()
    assert stats["processed"] == 0
    row = db.conn.execute("SELECT * FROM poll_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "failed"
