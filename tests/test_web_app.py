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
        db.upsert_video(Video(
            "v1", "MS4w_test", "标题", "https://d/v1", publish_time=100,
        ))
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


def test_default_web_pull_uses_all_user_runner(tmp_path):
    cfg = _cfg(tmp_path)
    seen = []

    def run_all(effective_cfg, *, limit):
        seen.append((effective_cfg.douyin.latest_count, limit))
        return {"new": 2, "processed": 2, "users": []}

    client = TestClient(create_app(cfg, all_users_runner=run_all))

    response = client.post("/api/pull").json()
    assert response["started"] is True
    for _ in range(200):
        status = client.get("/api/videos").json()["pull"]
        if not status["running"]:
            break
        time.sleep(0.005)

    assert seen == [(10, 10)]
    assert status["last_result"]["processed"] == 2


def test_pull_rejects_when_no_user_is_enabled(tmp_path):
    cfg = _cfg(tmp_path)
    calls = []
    client = TestClient(create_app(
        cfg, all_users_runner=lambda *_args, **_kwargs: calls.append(True),
    ))
    user = client.get("/api/users").json()["users"][0]
    client.put(f"/api/users/{user['id']}", json={
        **user, "enabled": False,
    })

    response = client.post("/api/pull")

    assert response.status_code == 400
    assert response.json()["error"] == "没有启用的用户"
    assert calls == []


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

def test_users_bootstrap_current_author_as_default(tmp_path):
    from ashare.llm.prompts import FINANCIAL_SYSTEM_PROMPT, GENERAL_SYSTEM_PROMPT

    c = _client(tmp_path, _PM())

    data = c.get("/api/users").json()

    assert len(data["users"]) == 1
    assert data["default_user_id"] == data["users"][0]["id"]
    assert data["users"][0]["sec_uid"] == "MS4w_test"
    assert data["users"][0]["analysis_type"] == "financial"
    assert data["users"][0]["system_prompt"] == FINANCIAL_SYSTEM_PROMPT
    assert data["default_prompts"] == {
        "financial": FINANCIAL_SYSTEM_PROMPT,
        "general": GENERAL_SYSTEM_PROMPT,
    }


def test_create_and_update_general_user(tmp_path):
    from ashare.llm.prompts import GENERAL_SYSTEM_PROMPT

    c = _client(tmp_path, _PM())
    created = c.post("/api/users", json={
        "sec_uid": "MS4w_general", "name": "科技博主",
        "analysis_type": "general",
    })

    assert created.status_code == 200
    user = created.json()["user"]
    assert user["system_prompt"] == GENERAL_SYSTEM_PROMPT

    updated = c.put(f"/api/users/{user['id']}", json={
        "name": "科技频道", "analysis_type": "general",
        "system_prompt": "自定义通用提示词", "enabled": False,
    })

    assert updated.status_code == 200
    saved = updated.json()["user"]
    assert saved["sec_uid"] == "MS4w_general"
    assert saved["name"] == "科技频道"
    assert saved["system_prompt"] == "自定义通用提示词"
    assert saved["enabled"] is False


def test_user_api_rejects_duplicate_and_invalid_payload(tmp_path):
    c = _client(tmp_path, _PM())

    duplicate = c.post("/api/users", json={
        "sec_uid": "MS4w_test", "name": "重复用户",
        "analysis_type": "financial", "system_prompt": "提示词",
    })
    invalid = c.post("/api/users", json={
        "sec_uid": "invalid", "name": "",
        "analysis_type": "unknown", "system_prompt": "",
    })
    non_boolean = c.post("/api/users", json={
        "sec_uid": "MS4w_bad_enabled", "name": "错误开关",
        "analysis_type": "general", "system_prompt": "提示词",
        "enabled": "false",
    })

    assert duplicate.status_code == 409
    assert invalid.status_code == 400
    assert non_boolean.status_code == 400


def test_videos_default_to_first_user_and_can_switch_filter(tmp_path):
    c = _client(tmp_path, _PM())
    other = c.post("/api/users", json={
        "sec_uid": "MS4w_other", "name": "其他用户",
        "analysis_type": "general", "system_prompt": "提示词",
    }).json()["user"]
    cfg = _cfg(tmp_path)
    db = Database(cfg.db_path)
    db.upsert_video(Video(
        "v2", "MS4w_other", "其他视频", "https://d/v2", publish_time=200,
    ))
    db.close()

    default_data = c.get("/api/videos").json()
    other_data = c.get(f"/api/videos?user_id={other['id']}").json()

    assert [video["aweme_id"] for video in default_data["videos"]] == ["v1"]
    assert [video["aweme_id"] for video in other_data["videos"]] == ["v2"]
    assert c.get("/api/videos?user_id=9999").status_code == 404

def test_get_settings_lists_editable(tmp_path):
    c = _client(tmp_path, _PM())
    data = c.get("/api/settings").json()
    keys = {s["key"] for s in data["settings"]}
    assert keys == {"cookie", "latest_count", "poll_interval_minutes",
                    "telegram_enabled", "telegram_bot_token", "telegram_chat_id",
                    "wecom_enabled"}
    assert "wecom_webhook_url" not in keys


def test_save_settings_persists(tmp_path):
    c = _client(tmp_path, _PM())
    r = c.post("/api/settings", json={"latest_count": 20})
    assert r.status_code == 200
    vals = {s["key"]: s["value"] for s in c.get("/api/settings").json()["settings"]}
    assert vals["latest_count"] == 20


def test_save_settings_rejects_bad_int(tmp_path):
    c = _client(tmp_path, _PM())
    r = c.post("/api/settings", json={"latest_count": "abc"})
    assert r.status_code == 400


def test_global_settings_ignore_legacy_author_secuid(tmp_path):
    c = _client(tmp_path, _PM())
    r = c.post("/api/settings", json={"author_sec_uid": "创业魔法师"})
    assert r.status_code == 200
    keys = {item["key"] for item in c.get("/api/settings").json()["settings"]}
    assert "author_sec_uid" not in keys


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


def test_delete_user_removes_own_data_and_keeps_other_user(tmp_path):
    cfg = _cfg(tmp_path)
    c = _client(tmp_path, _PM())
    target = c.get("/api/users").json()["users"][0]
    other = c.post("/api/users", json={
        "sec_uid": "MS4w_other", "name": "保留用户",
        "analysis_type": "general", "system_prompt": "提示词",
    }).json()["user"]
    db = Database(cfg.db_path)
    db.upsert_video(Video(
        "v2", other["sec_uid"], "保留视频", "https://d/v2", publish_time=200,
    ))
    db.close()
    target_dir = cfg.videos_dir / "1970-01-01_v1"
    other_dir = cfg.videos_dir / "1970-01-01_v2"
    target_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)
    (target_dir / "video.mp4").write_bytes(b"target")
    (other_dir / "video.mp4").write_bytes(b"other")

    response = c.delete(f"/api/users/{target['id']}")

    assert response.status_code == 200
    assert response.json()["deleted_videos"] == 1
    assert response.json()["file_errors"] == []
    assert not target_dir.exists()
    assert other_dir.exists()
    users = c.get("/api/users").json()["users"]
    assert [user["id"] for user in users] == [other["id"]]
    assert c.get(f"/api/videos?user_id={other['id']}").json()["stats"]["total"] == 1
    assert c.delete(f"/api/users/{target['id']}").status_code == 404


def test_delete_last_user_remains_empty_after_app_restart(tmp_path):
    cfg = _cfg(tmp_path)
    c = _client(tmp_path, _PM())
    user = c.get("/api/users").json()["users"][0]

    assert c.delete(f"/api/users/{user['id']}").status_code == 200
    assert c.get("/api/users").json()["users"] == []

    restarted = TestClient(create_app(cfg, pull_manager=_PM()))
    assert restarted.get("/api/users").json()["users"] == []


def test_delete_user_rejects_while_pull_is_running(tmp_path):
    import threading

    gate = threading.Event()
    pm = PullManager(
        run_fn=lambda: (gate.wait(timeout=2), {"new": 0, "processed": 0})[1],
    )
    c = _client(tmp_path, pm)
    user = c.get("/api/users").json()["users"][0]
    assert c.post("/api/pull").json()["started"] is True
    for _ in range(200):
        if pm.status()["running"]:
            break
        time.sleep(0.005)

    try:
        response = c.delete(f"/api/users/{user['id']}")
    finally:
        gate.set()

    assert response.status_code == 409
    assert "拉取处理中" in response.json()["error"]
    assert c.get(f"/api/users").json()["users"][0]["id"] == user["id"]


def test_clear_all_videos(tmp_path):
    c = _client(tmp_path, _PM())
    assert c.delete("/api/videos").json()["ok"] is True
    assert c.get("/api/videos").json()["stats"]["total"] == 0
    assert len(c.get("/api/users").json()["users"]) == 1


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


def test_dashboard_contains_user_selector_and_prompt_editor():
    html = (Path(__file__).parents[1] / "src/ashare/web/templates/dashboard.html").read_text()

    assert 'id="userSelect"' in html
    assert 'id="userForm"' in html
    assert 'id="userAnalysisType"' in html
    assert 'id="userSystemPrompt"' in html
    assert 'id="resetUserPrompt"' in html
    assert 'id="deleteUserBtn"' in html
    assert 'id="userDanger"' in html
    assert 'fetchJSON("/api/users")' in html
    assert '/api/users/${userId}' in html
    assert 'user_id=' in html


def test_dashboard_conditionally_renders_video_duration():
    html = (Path(__file__).parents[1] / "src/ashare/web/templates/dashboard.html").read_text()

    assert "v.duration_label?" in html
    assert "d.duration_label?" in html
