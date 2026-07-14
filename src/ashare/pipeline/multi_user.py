"""按数据库用户顺序串行执行一轮，并在整轮结束后释放共享 ASR。"""

from __future__ import annotations

from typing import Callable

from ..asr.base import Transcriber, make_transcriber
from ..config import AppConfig
from ..db import Database
from ..models import TrackedUser
from ..users import ensure_initial_tracked_user
from .runner import Pipeline, build_pipeline

PipelineFactory = Callable[
    [AppConfig, Database, Transcriber, TrackedUser], Pipeline,
]


def run_all_users(
    cfg: AppConfig, *, limit: int | None = None, db: Database | None = None,
    pipeline_factory: PipelineFactory | None = None,
    transcriber_factory: Callable = make_transcriber,
) -> dict:
    """处理全部启用用户；单用户异常只记入结果，不阻断后续用户。"""
    database = db or Database(cfg.db_path)
    owns_database = db is None
    transcriber = None
    try:
        ensure_initial_tracked_user(database, cfg)
        users = database.list_tracked_users(enabled_only=True)
        if not users:
            return {
                "new": 0, "processed": 0, "users": [],
                "error": "没有启用用户",
            }
        transcriber = transcriber_factory(cfg.asr)
        build = pipeline_factory or _build_user_pipeline
        results = [
            _run_user(cfg, database, transcriber, user, build, limit)
            for user in users
        ]
        return _aggregate(results)
    finally:
        try:
            if transcriber is not None:
                transcriber.release()
        finally:
            if owns_database:
                database.close()


def _run_user(
    cfg: AppConfig, db: Database, transcriber: Transcriber,
    user: TrackedUser, factory: PipelineFactory, limit: int | None,
) -> dict:
    try:
        stats = factory(_config_for_user(cfg, user), db, transcriber, user).run_once(limit)
    except Exception as error:
        stats = {"new": 0, "processed": 0, "error": str(error)}
    return {"user_id": user.id, "name": user.name, **stats}


def _config_for_user(cfg: AppConfig, user: TrackedUser) -> AppConfig:
    user_cfg = cfg.model_copy(deep=True)
    user_cfg.douyin.author_sec_uid = user.sec_uid
    user_cfg.douyin.author_name = user.name
    return user_cfg


def _build_user_pipeline(
    cfg: AppConfig, db: Database, transcriber: Transcriber, user: TrackedUser,
) -> Pipeline:
    return build_pipeline(
        cfg, db=db, transcriber=transcriber,
        analysis_type=user.analysis_type, system_prompt=user.system_prompt,
    )


def _aggregate(results: list[dict]) -> dict:
    return {
        "new": sum(item.get("new", 0) for item in results),
        "processed": sum(item.get("processed", 0) for item in results),
        "users": results,
    }
