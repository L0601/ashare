"""db 层单测：增改查、阶段状态流转、观点幂等替换。

用内存库 + 注入的固定时钟，保证不依赖 wall clock、可重复。
"""

import pytest

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


@pytest.fixture
def db():
    # 固定时钟，断言时间字段确定
    database = Database(":memory:", clock=lambda: 1000)
    yield database
    database.close()


def _video(aweme_id="v1", status=Status.PENDING):
    return Video(
        aweme_id=aweme_id, author_sec_uid="sec1", title="标题",
        video_url="https://douyin.com/video/v1", publish_time=900,
        overall_status=status,
    )


def test_upsert_and_get_video(db):
    db.upsert_video(_video())
    got = db.get_video("v1")
    assert got is not None
    assert got.title == "标题"
    assert got.discovered_at == 1000
    assert got.overall_status == Status.PENDING


def test_list_videos_sorted_by_publish_time_desc(db):
    db.upsert_video(Video("a", "s", "A", "u", publish_time=100))
    db.upsert_video(Video("b", "s", "B", "u", publish_time=200))
    ids = [v.aweme_id for v in db.list_videos()]
    assert ids == ["b", "a"]


def test_known_aweme_ids(db):
    db.upsert_video(_video("v1"))
    db.upsert_video(_video("v2"))
    assert db.known_aweme_ids() == {"v1", "v2"}


def test_set_video_status(db):
    db.upsert_video(_video())
    db.set_video_status("v1", Status.SUCCESS)
    assert db.get_video("v1").overall_status == Status.SUCCESS


def test_stage_status_default_pending(db):
    assert db.get_stage_status("v1", Stage.DOWNLOAD) == Status.PENDING


def test_stage_transition_and_attempts(db):
    db.set_stage("v1", Stage.DOWNLOAD, Status.RUNNING)
    db.set_stage("v1", Stage.DOWNLOAD, Status.SUCCESS)
    assert db.get_stage_status("v1", Stage.DOWNLOAD) == Status.SUCCESS
    stages = db.get_stages("v1")
    assert stages["download"]["attempts"] == 1
    assert stages["download"]["finished_at"] == 1000


def test_stage_records_error_on_failure(db):
    db.set_stage("v1", Stage.TRANSCRIBE, Status.RUNNING)
    db.set_stage("v1", Stage.TRANSCRIBE, Status.FAILED, error="模型加载失败")
    stages = db.get_stages("v1")
    assert stages["transcribe"]["status"] == "failed"
    assert stages["transcribe"]["error"] == "模型加载失败"


def test_save_and_get_transcript(db):
    db.save_transcript(Transcript("v1", "今天看好新能源", "fake", audio_seconds=12.5))
    t = db.get_transcript("v1")
    assert t.text == "今天看好新能源"
    assert t.audio_seconds == 12.5
    assert t.created_at == 1000


def test_save_analysis_with_opinions(db):
    analysis = Analysis(
        aweme_id="v1", llm_model="deepseek-chat", prompt_input="...", raw_output="{...}",
        summary="整体看多新能源", sectors=["新能源", "光伏"],
        opinions=[
            Opinion("宁德时代", TargetType.STOCK, Stance.BULLISH, "电池龙头",
                    target_code="300750", confidence=0.8, quote="我看好宁王"),
            Opinion("光伏", TargetType.INDUSTRY, Stance.BEARISH, "产能过剩"),
        ],
    )
    db.save_analysis(analysis)
    got = db.get_analysis("v1")
    assert got.summary == "整体看多新能源"
    assert got.sectors == ["新能源", "光伏"]
    assert len(got.opinions) == 2
    assert got.opinions[0].target_name == "宁德时代"
    assert got.opinions[0].stance == Stance.BULLISH
    assert got.opinions[1].target_type == TargetType.INDUSTRY


def test_save_analysis_is_idempotent_on_opinions(db):
    """重复保存同一视频的分析，观点应被替换而非累加。"""
    base = Analysis("v1", "m", "in", "out",
                    opinions=[Opinion("A", TargetType.STOCK, Stance.BULLISH, "x")])
    db.save_analysis(base)
    base.opinions = [Opinion("B", TargetType.STOCK, Stance.BEARISH, "y")]
    db.save_analysis(base)
    ops = db.get_opinions("v1")
    assert len(ops) == 1
    assert ops[0].target_name == "B"


def test_upsert_does_not_wipe_title_with_empty(db):
    """复现 bug：视频无文案(title='')→LLM 回填标题→再次 discover(title 仍空)不应抹掉标题。"""
    db.upsert_video(Video("v1", "s", "", "u", publish_time=100))
    db.set_video_title("v1", "LLM生成的标题")
    # 再次发现同一视频，抖音返回的 desc 仍是空
    db.upsert_video(Video("v1", "s", "", "u2", publish_time=100))
    assert db.get_video("v1").title == "LLM生成的标题"


def test_upsert_updates_title_when_incoming_nonempty(db):
    """有真实文案时，应正常更新标题（不被旧值卡住）。"""
    db.upsert_video(Video("v1", "s", "旧文案", "u", publish_time=100))
    db.upsert_video(Video("v1", "s", "新文案", "u", publish_time=100))
    assert db.get_video("v1").title == "新文案"


def test_video_author_name_persisted(db):
    db.upsert_video(Video("v1", "s", "标题", "u", publish_time=100, author_name="创业魔法师"))
    assert db.get_video("v1").author_name == "创业魔法师"
    # 再次 discover 传空作者名不应覆盖
    db.upsert_video(Video("v1", "s", "标题", "u", publish_time=100, author_name=""))
    assert db.get_video("v1").author_name == "创业魔法师"


def test_migrate_adds_author_name_to_old_db(tmp_path):
    """老库（无 author_name 列）打开时应自动补列，不报错。"""
    import sqlite3
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.execute("""CREATE TABLE videos (aweme_id TEXT PRIMARY KEY, author_sec_uid TEXT NOT NULL,
        title TEXT NOT NULL, video_url TEXT NOT NULL, publish_time INTEGER NOT NULL,
        cover_url TEXT DEFAULT '', duration_ms INTEGER DEFAULT 0,
        overall_status TEXT NOT NULL DEFAULT 'pending', discovered_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL)""")
    con.execute("INSERT INTO videos VALUES ('v1','s','t','u',100,'',0,'pending',1,1)")
    con.commit(); con.close()
    database = Database(p, clock=lambda: 1000)   # 打开即迁移
    cols = {r["name"] for r in database.conn.execute("PRAGMA table_info(videos)")}
    assert "author_name" in cols
    assert database.get_video("v1") is not None   # 老数据可正常读
    database.close()


def test_settings_get_set(db):
    assert db.get_settings() == {}
    db.set_settings({"cookie": "ck", "latest_count": "10"})
    assert db.get_settings() == {"cookie": "ck", "latest_count": "10"}
    # 更新已有键，None 跳过不覆盖
    db.set_settings({"cookie": "ck2", "author_sec_uid": None})
    s = db.get_settings()
    assert s["cookie"] == "ck2"
    assert "author_sec_uid" not in s


def test_delete_video_removes_all_records(db):
    db.upsert_video(_video("v1"))
    db.set_stage("v1", Stage.DOWNLOAD, Status.SUCCESS)
    db.save_transcript(Transcript("v1", "文本", "fake"))
    db.save_analysis(Analysis("v1", "m", "in", "out",
                              opinions=[Opinion("A", TargetType.STOCK, Stance.BULLISH, "x")]))
    db.upsert_video(_video("v2"))   # 另一条不受影响
    db.delete_video("v1")
    assert db.get_video("v1") is None
    assert db.get_transcript("v1") is None
    assert db.get_analysis("v1") is None
    assert db.get_opinions("v1") == []
    assert db.get_stage_status("v1", Stage.DOWNLOAD) == Status.PENDING
    assert db.get_video("v2") is not None   # v2 还在


def test_reset_keeps_settings(db):
    """清空数据不应清掉运行时配置（cookie/sec_uid 等）。"""
    db.set_settings({"cookie": "ck"})
    db.upsert_video(_video("v1"))
    db.reset()
    assert db.list_videos() == []
    assert db.get_settings() == {"cookie": "ck"}   # 配置保留


def test_reset_clears_all_data(db):
    db.upsert_video(_video())
    db.set_stage("v1", Stage.DOWNLOAD, Status.SUCCESS)
    db.save_transcript(Transcript("v1", "文本", "fake"))
    assert db.list_videos()
    db.reset()
    # 表仍在（可正常查询）但数据已清空
    assert db.list_videos() == []
    assert db.known_aweme_ids() == set()
    assert db.get_transcript("v1") is None
    assert db.get_stage_status("v1", Stage.DOWNLOAD) == Status.PENDING


def test_poll_run_lifecycle(db):
    pid = db.start_poll()
    db.finish_poll(pid, new_count=3, status="success")
    row = db.conn.execute("SELECT * FROM poll_runs WHERE id=?", (pid,)).fetchone()
    assert row["new_count"] == 3
    assert row["status"] == "success"
    assert row["finished_at"] == 1000
