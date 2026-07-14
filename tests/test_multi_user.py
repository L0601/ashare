"""全用户协调器：串行、隔离、失败继续，并统一管理 ASR 生命周期。"""

from types import SimpleNamespace

from ashare.config import AppConfig, ASRConfig, DouyinConfig, LLMConfig
from ashare.db import Database


def _cfg() -> AppConfig:
    return AppConfig(
        douyin=DouyinConfig(cookie="ck", latest_count=5),
        llm=LLMConfig(base_url="http://x", api_key="k", model="m"),
        asr=ASRConfig(engine="fake"),
    )


def _seed_users(db: Database):
    first = db.create_tracked_user(
        sec_uid="MS4w_first", name="第一位", analysis_type="financial",
        system_prompt="金融提示词",
    )
    second = db.create_tracked_user(
        sec_uid="MS4w_second", name="第二位", analysis_type="general",
        system_prompt="通用提示词",
    )
    disabled = db.create_tracked_user(
        sec_uid="MS4w_disabled", name="已停用", analysis_type="general",
        system_prompt="停用提示词", enabled=False,
    )
    return first, second, disabled


def test_run_all_users_is_ordered_and_releases_shared_transcriber_once():
    from ashare.pipeline.multi_user import run_all_users

    db = Database(":memory:", clock=lambda: 1000)
    first, second, _ = _seed_users(db)
    transcriber = SimpleNamespace(release_count=0)
    transcriber.release = lambda: setattr(
        transcriber, "release_count", transcriber.release_count + 1,
    )
    seen = []

    def pipeline_factory(user_cfg, database, shared, user):
        seen.append((
            user_cfg.douyin.author_sec_uid, user.analysis_type.value,
            user.system_prompt, shared,
        ))
        amount = 1 if user.id == first.id else 2
        return SimpleNamespace(run_once=lambda limit: {
            "new": amount, "processed": amount,
        })

    result = run_all_users(
        _cfg(), db=db, limit=3, pipeline_factory=pipeline_factory,
        transcriber_factory=lambda _: transcriber,
    )

    assert [item[0] for item in seen] == ["MS4w_first", "MS4w_second"]
    assert [item[1] for item in seen] == ["financial", "general"]
    assert [item[2] for item in seen] == ["金融提示词", "通用提示词"]
    assert all(item[3] is transcriber for item in seen)
    assert result["new"] == 3
    assert result["processed"] == 3
    assert [item["user_id"] for item in result["users"]] == [first.id, second.id]
    assert transcriber.release_count == 1


def test_run_all_users_continues_after_one_user_raises():
    from ashare.pipeline.multi_user import run_all_users

    db = Database(":memory:", clock=lambda: 1000)
    first, second, _ = _seed_users(db)
    calls = []

    def pipeline_factory(user_cfg, database, shared, user):
        calls.append(user.id)
        if user.id == first.id:
            return SimpleNamespace(run_once=lambda limit: (_ for _ in ()).throw(
                RuntimeError("首用户异常"),
            ))
        return SimpleNamespace(run_once=lambda limit: {"new": 1, "processed": 1})

    result = run_all_users(
        _cfg(), db=db, pipeline_factory=pipeline_factory,
        transcriber_factory=lambda _: SimpleNamespace(release=lambda: None),
    )

    assert calls == [first.id, second.id]
    assert "首用户异常" in result["users"][0]["error"]
    assert result["users"][1]["processed"] == 1
