"""看板数据装配单测：摘要、统计聚合、详情、阶段补全、时间格式化。"""

from ashare.db import Database
from ashare.models import (
    Analysis,
    Opinion,
    Stage,
    Stance,
    Status,
    TargetType,
    Transcript,
    Video,
)
from ashare.web.service import dashboard_data, fmt_time, video_detail, video_summary


def _seed():
    db = Database(":memory:", clock=lambda: 1700000000)
    db.upsert_video(Video("v1", "sec", "看好半导体", "https://d/v1", publish_time=1704135845))
    db.set_video_status("v1", Status.SUCCESS)
    db.set_stage("v1", Stage.DISCOVER, Status.SUCCESS)
    db.set_stage("v1", Stage.DOWNLOAD, Status.SUCCESS)
    db.save_transcript(Transcript("v1", "我觉得半导体要起来了", "fake", audio_seconds=30))
    db.save_analysis(Analysis(
        "v1", "deepseek-chat", "输入prompt", '{"raw":1}', summary="整体看多半导体",
        sectors=["半导体"],
        opinions=[Opinion("中芯国际", TargetType.STOCK, Stance.BULLISH, "国产替代",
                          target_code="688981", confidence=0.75, quote="看好它")],
    ))
    return db


def test_fmt_time():
    assert fmt_time(0) == "-"
    assert fmt_time(1704135845) == "2024-01-02 03:04:05"


def test_video_summary():
    db = _seed()
    s = video_summary(db, "v1")
    assert s["title"] == "看好半导体"
    assert s["overall_status"] == "success"
    assert s["opinion_count"] == 1
    assert s["sectors"] == ["半导体"]
    # 阶段补全到 6 个（含推送），未跑的为 pending
    assert len(s["stages"]) == 6
    statuses = {st["stage"]: st["status"] for st in s["stages"]}
    assert statuses["download"] == "success"
    assert statuses["transcribe"] == "pending"


def test_stage_list_includes_second_precision_and_duration():
    now = {"ts": 1700000000}
    db = Database(":memory:", clock=lambda: now["ts"])
    db.upsert_video(Video("v1", "sec", "t", "https://d/v1", publish_time=1704135845))
    db.set_stage("v1", Stage.DOWNLOAD, Status.RUNNING)
    now["ts"] += 7
    db.set_stage("v1", Stage.DOWNLOAD, Status.SUCCESS)

    stage = {st["stage"]: st for st in video_summary(db, "v1")["stages"]}["download"]

    assert stage["started_at"] == "2023-11-15 06:13:20"
    assert stage["finished_at"] == "2023-11-15 06:13:27"
    assert stage["duration_seconds"] == 7
    assert stage["duration_label"] == "7s"


def test_dashboard_stats():
    db = _seed()
    data = dashboard_data(db)
    assert data["stats"]["total"] == 1
    assert data["stats"]["success"] == 1
    assert data["stats"]["opinions"] == 1


def test_video_detail():
    db = _seed()
    d = video_detail(db, "v1")
    assert d["transcript"] == "我觉得半导体要起来了"
    assert d["summary"] == "整体看多半导体"
    assert d["prompt_input"] == "输入prompt"
    assert d["raw_output"] == '{"raw":1}'
    assert d["opinions"][0]["target_name"] == "中芯国际"
    assert d["opinions"][0]["stance"] == "bullish"
    assert d["audio_seconds"] == 30


def test_video_detail_missing():
    assert video_detail(_seed(), "nope") is None
