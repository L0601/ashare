"""多用户初始化：旧单用户配置只迁移一次，不覆盖后续编辑。"""

from ashare.config import AppConfig, DouyinConfig, LLMConfig
from ashare.db import Database
from ashare.llm.prompts import SYSTEM_PROMPT
from ashare.models import AnalysisType, Video


def _cfg(sec_uid: str = "MS4w_file") -> AppConfig:
    return AppConfig(
        douyin=DouyinConfig(author_sec_uid=sec_uid, author_name="文件昵称"),
        llm=LLMConfig(base_url="http://x", api_key="k", model="m"),
    )


def test_initial_user_prefers_legacy_db_setting_and_video_name():
    from ashare.users import ensure_initial_tracked_user

    db = Database(":memory:", clock=lambda: 1000)
    db.set_settings({"author_sec_uid": "MS4w_database"})
    db.upsert_video(Video(
        "v1", "MS4w_database", "标题", "u", publish_time=100,
        author_name="数据库昵称",
    ))

    user = ensure_initial_tracked_user(db, _cfg())

    assert user.sec_uid == "MS4w_database"
    assert user.name == "数据库昵称"
    assert user.analysis_type == AnalysisType.FINANCIAL
    assert user.system_prompt == SYSTEM_PROMPT
    assert "author_sec_uid" not in db.get_settings()


def test_initial_user_is_idempotent_and_keeps_custom_prompt():
    from ashare.users import ensure_initial_tracked_user

    db = Database(":memory:", clock=lambda: 1000)
    first = ensure_initial_tracked_user(db, _cfg())
    db.update_tracked_user(
        first.id, name=first.name, analysis_type="general",
        system_prompt="用户自定义提示词", enabled=True,
    )

    second = ensure_initial_tracked_user(db, _cfg("MS4w_changed"))

    assert second.id == first.id
    assert second.analysis_type == AnalysisType.GENERAL
    assert second.system_prompt == "用户自定义提示词"
    assert len(db.list_tracked_users()) == 1


def test_deleted_last_user_is_not_recreated_from_legacy_config():
    from ashare.users import ensure_initial_tracked_user

    db = Database(":memory:", clock=lambda: 1000)
    first = ensure_initial_tracked_user(db, _cfg())

    db.delete_tracked_user(first.id)
    recreated = ensure_initial_tracked_user(db, _cfg("MS4w_changed"))

    assert recreated is None
    assert db.list_tracked_users() == []
