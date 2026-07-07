"""Web 接口测试：/api/videos 附带 pull 状态、/api/pull 触发拉取。

用注入的 PullManager（fake run_fn），不真正建流水线、不联网。
"""

import time
from pathlib import Path

from fastapi.testclient import TestClient

from ashare.config import AppConfig, ASRConfig, DouyinConfig, LLMConfig, StorageConfig
from ashare.db import Database
from ashare.douyin.base import FakeDouyinClient
from ashare.models import Status, Video
from ashare.web.app import create_app
from ashare.web.pull import PullManager


def _cfg(tmp_path):
    return AppConfig(
        douyin=DouyinConfig(cookie="x", author_sec_uid="MS4w_test"),
        llm=LLMConfig(base_url="http://x", api_key="k", model="m"),
        asr=ASRConfig(engine="fake"),
        storage=StorageConfig(data_dir=str(tmp_path / "data")),
    )


def _client(tmp_path, pull_manager, client_factory=None, seed=True):
    cfg = _cfg(tmp_path)
    if seed:
        db = Database(cfg.db_path)
        db.upsert_video(Video("v1", "s", "标题", "https://d/v1", publish_time=100))
        db.set_video_status("v1", Status.SUCCESS)
        db.close()
    kw = {"pull_manager": pull_manager}
    if client_factory:
        kw["client_factory"] = client_factory
    return TestClient(create_app(cfg, **kw))


_PM = lambda: PullManager(run_fn=lambda: {"new": 0, "processed": 0})  # noqa: E731


def test_videos_includes_pull_status(tmp_path):
    pm = PullManager(run_fn=lambda: {"new": 0, "processed": 0})
    client = _client(tmp_path, pm)
    data = client.get("/api/videos").json()
    assert "pull" in data
    assert data["pull"]["running"] is False
    assert data["stats"]["total"] == 1


def test_pull_triggers_and_reports_result(tmp_path):
    pm = PullManager(run_fn=lambda: {"new": 3, "processed": 2})
    client = _client(tmp_path, pm)
    r = client.post("/api/pull").json()
    assert r["started"] is True
    # 等后台线程跑完
    for _ in range(200):
        if not pm.status()["running"]:
            break
        time.sleep(0.005)
    assert pm.status()["last_result"] == {"new": 3, "processed": 2}


def test_pull_single_flight_second_call_not_started(tmp_path):
    import threading
    gate = threading.Event()
    pm = PullManager(run_fn=lambda: (gate.wait(timeout=2), {"new": 0, "processed": 0})[1])
    client = _client(tmp_path, pm)
    assert client.post("/api/pull").json()["started"] is True
    # 运行中再次触发应 started=false
    for _ in range(200):
        if pm.status()["running"]:
            break
        time.sleep(0.005)
    assert client.post("/api/pull").json()["started"] is False
    gate.set()


# ---------- 设置 / 数据管理 ----------

def test_get_settings_lists_editable(tmp_path):
    c = _client(tmp_path, _PM())
    data = c.get("/api/settings").json()
    keys = {s["key"] for s in data["settings"]}
    assert keys == {"cookie", "author_sec_uid", "latest_count", "poll_interval_minutes",
                    "telegram_enabled", "telegram_bot_token", "telegram_chat_id"}


def test_save_settings_persists(tmp_path):
    c = _client(tmp_path, _PM())
    r = c.post("/api/settings", json={"latest_count": 20, "author_sec_uid": "MS4wABC"})
    assert r.status_code == 200
    vals = {s["key"]: s["value"] for s in c.get("/api/settings").json()["settings"]}
    assert vals["latest_count"] == 20
    assert vals["author_sec_uid"] == "MS4wABC"


def test_save_settings_rejects_bad_int(tmp_path):
    c = _client(tmp_path, _PM())
    r = c.post("/api/settings", json={"latest_count": "abc"})
    assert r.status_code == 400


def test_save_settings_rejects_bad_secuid(tmp_path):
    c = _client(tmp_path, _PM())
    r = c.post("/api/settings", json={"author_sec_uid": "创业魔法师"})
    assert r.status_code == 400
    assert "MS4w" in r.json()["error"]


def test_validate_secuid_returns_profile(tmp_path):
    fake = FakeDouyinClient(sec_uid="MS4wTESTID", videos=[])
    c = _client(tmp_path, _PM(), client_factory=lambda cfg: fake)
    r = c.post("/api/settings/validate-secuid", json={"value": "MS4wTESTID"}).json()
    assert r["sec_uid"] == "MS4wTESTID"
    assert r["nickname"] == "测试博主"
    assert r["aweme_count"] == 42


def test_delete_one_video(tmp_path):
    c = _client(tmp_path, _PM())
    assert c.delete("/api/videos/v1").json()["ok"] is True
    assert c.get("/api/videos").json()["stats"]["total"] == 0


def test_delete_one_video_removes_dated_video_dir(tmp_path):
    cfg = _cfg(tmp_path)
    vdir = cfg.videos_dir / "1970-01-01_v1"
    vdir.mkdir(parents=True)
    (vdir / "video.mp4").write_bytes(b"x")

    c = _client(tmp_path, _PM())

    assert c.delete("/api/videos/v1").json()["ok"] is True
    assert not vdir.exists()


def test_clear_all_videos(tmp_path):
    c = _client(tmp_path, _PM())
    assert c.delete("/api/videos").json()["ok"] is True
    assert c.get("/api/videos").json()["stats"]["total"] == 0


def test_videos_includes_auto_status(tmp_path):
    c = _client(tmp_path, _PM())
    data = c.get("/api/videos").json()
    assert "auto" in data
    assert data["auto"]["enabled"] is False
    assert data["auto"]["interval_minutes"] == 90    # 文件默认


def test_toggle_auto_pull_persists(tmp_path):
    c = _client(tmp_path, _PM())
    on = c.post("/api/auto-pull", json={"enabled": True}).json()
    assert on["enabled"] is True
    assert on["interval_minutes"] == 90
    assert c.get("/api/videos").json()["auto"]["enabled"] is True
    off = c.post("/api/auto-pull", json={"enabled": False}).json()
    assert off["enabled"] is False
    assert off["interval_minutes"] == 90
    assert c.get("/api/videos").json()["auto"]["enabled"] is False


def test_auto_indicator_hidden_attribute_is_not_overridden():
    html = (Path(__file__).parents[1] / "src/ashare/web/templates/dashboard.html").read_text()

    assert ".auto-ind[hidden]" in html
