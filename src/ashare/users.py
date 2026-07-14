"""受监控用户的旧配置初始化与运行时装配。"""

from __future__ import annotations

from .config import AppConfig
from .db import Database
from .llm.prompts import SYSTEM_PROMPT
from .models import AnalysisType, TrackedUser


def ensure_initial_tracked_user(
    db: Database, cfg: AppConfig, *, default_prompt: str = SYSTEM_PROMPT,
) -> TrackedUser | None:
    """用户表为空时把旧单用户配置迁入数据库；之后不再覆盖。"""
    users = db.list_tracked_users()
    if users:
        return users[0]
    settings = db.get_settings()
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
