"""受监控用户的旧配置初始化与运行时装配。"""

from __future__ import annotations

from .config import AppConfig
from .db import Database, TRACKED_USERS_INITIALIZED_KEY
from .llm.prompts import SYSTEM_PROMPT
from .models import AnalysisType, TrackedUser


def ensure_initial_tracked_user(
    db: Database, cfg: AppConfig, *, default_prompt: str = SYSTEM_PROMPT,
) -> TrackedUser | None:
    """首次启动时迁移旧单用户配置；显式删空后不再自动创建。"""
    settings = db.get_settings()
    users = db.list_tracked_users()
    if users:
        if settings.get(TRACKED_USERS_INITIALIZED_KEY) != "1":
            db.set_settings({TRACKED_USERS_INITIALIZED_KEY: "1"})
        return users[0]
    if settings.get(TRACKED_USERS_INITIALIZED_KEY) == "1":
        return None
    sec_uid = (settings.get("author_sec_uid") or cfg.douyin.author_sec_uid).strip()
    if not sec_uid.startswith("MS4w"):
        return None
    name = db.latest_author_name(sec_uid) or cfg.douyin.author_name or sec_uid
    user = db.create_tracked_user(
        sec_uid=sec_uid, name=name, analysis_type=AnalysisType.FINANCIAL,
        system_prompt=default_prompt,
    )
    db.delete_setting("author_sec_uid")
    return user
