"""FastAPI 后台：看板(只读展示) + 设置/数据管理(写操作)。

配置分两层:文件(固化项) + DB settings(运行时可改项),最终用 apply_overrides 合并。
后台改完即时生效(每次操作都重新解析 effective config),无需重启。
绑定 127.0.0.1,单用户本地工具,不加鉴权;破坏性操作由前端二次确认。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import (
    EDITABLE_SETTINGS,
    AppConfig,
    apply_overrides,
    editable_values,
    load_config,
)
from ..db import Database
from ..douyin.base import DouyinClient
from ..storage import video_dir
from .pull import PullManager
from .service import dashboard_data, video_detail

_INDEX_HTML = (Path(__file__).parent / "templates" / "dashboard.html").read_text(encoding="utf-8")


def _default_client_factory(cfg: AppConfig) -> DouyinClient:
    from ..douyin.client import F2DouyinClient

    return F2DouyinClient(cfg)


def create_app(
    cfg: AppConfig | None = None,
    pull_manager: PullManager | None = None,
    client_factory: Callable[[AppConfig], DouyinClient] = _default_client_factory,
) -> FastAPI:
    file_cfg = cfg or load_config()
    app = FastAPI(title="抖音金融观点看板", docs_url=None, redoc_url=None)

    def open_db() -> Database:
        return Database(file_cfg.db_path)

    def effective() -> AppConfig:
        """文件配置 + DB 运行时覆盖（每次取最新，后台改完即生效）。"""
        db = open_db()
        try:
            return apply_overrides(file_cfg, db.get_settings())
        finally:
            db.close()

    def run_pull() -> dict:
        from ..pipeline.runner import build_pipeline

        eff = effective()
        # 合并后："最近 N 条"既是抓取数也是处理数
        return build_pipeline(eff).run_once(limit=eff.douyin.latest_count)

    pull = pull_manager or PullManager(run_pull)

    # ---------- 页面 ----------
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    # ---------- 看板数据 ----------
    @app.get("/api/videos")
    def api_videos():
        db = open_db()
        try:
            data = dashboard_data(db)
            data["pull"] = pull.status()
            s = db.get_settings()
            eff = apply_overrides(file_cfg, s)
            # 自动拉取开关由前端定时驱动（serve 自身不轮询抖音）
            data["auto"] = {"enabled": s.get("auto_pull") == "1",
                            "interval_minutes": eff.schedule.poll_interval_minutes}
            return data
        finally:
            db.close()

    @app.post("/api/auto-pull")
    def api_auto_pull(payload: dict = Body(...)):
        enabled = bool(payload.get("enabled"))
        db = open_db()
        try:
            db.set_settings({"auto_pull": "1" if enabled else "0"})
        finally:
            db.close()
        return {"enabled": enabled}

    @app.get("/api/videos/{aweme_id}")
    def api_detail(aweme_id: str):
        db = open_db()
        try:
            data = video_detail(db, aweme_id)
            return data or JSONResponse({"error": "视频不存在"}, status_code=404)
        finally:
            db.close()

    # ---------- 拉取 ----------
    @app.post("/api/pull")
    def api_pull():
        started = pull.trigger()
        return {"started": started, "pull": pull.status()}

    # ---------- 设置 ----------
    @app.get("/api/settings")
    def api_get_settings():
        vals = editable_values(effective())
        return {"settings": [
            {**spec, "value": vals[spec["key"]]} for spec in EDITABLE_SETTINGS
        ]}

    @app.post("/api/settings")
    def api_save_settings(payload: dict = Body(...)):
        ok, err = _validate_settings(payload)
        if not ok:
            return JSONResponse({"error": err}, status_code=400)
        db = open_db()
        try:
            db.set_settings({s["key"]: payload.get(s["key"]) for s in EDITABLE_SETTINGS
                             if s["key"] in payload})
        finally:
            db.close()
        return {"ok": True, "values": editable_values(effective())}

    @app.post("/api/settings/validate-secuid")
    def api_validate_secuid(payload: dict = Body(...)):
        client = client_factory(effective())
        resolved = client.resolve_sec_uid(str(payload.get("value", "")).strip())
        if not resolved.is_ok():
            return JSONResponse({"error": resolved.error}, status_code=400)
        sec = resolved.value
        if not sec.startswith("MS4w"):
            return JSONResponse({"error": f"解析出的不是合法 sec_uid：{sec}"}, status_code=400)
        prof = client.fetch_profile(sec)
        if not prof.is_ok():
            return JSONResponse({"error": prof.error}, status_code=400)
        return {"sec_uid": sec, **prof.value}

    # ---------- 数据管理 ----------
    @app.delete("/api/videos")
    def api_clear_all():
        db = open_db()
        try:
            db.reset()
        finally:
            db.close()
        if file_cfg.videos_dir.exists():
            shutil.rmtree(file_cfg.videos_dir)
        return {"ok": True}

    @app.delete("/api/videos/{aweme_id}")
    def api_delete_one(aweme_id: str):
        db = open_db()
        try:
            video = db.get_video(aweme_id)
            db.delete_video(aweme_id)
        finally:
            db.close()
        vdir = video_dir(file_cfg.videos_dir, video, aweme_id)
        if vdir.exists():
            shutil.rmtree(vdir)
        return {"ok": True}

    return app


def _validate_settings(payload: dict) -> tuple[bool, str]:
    """校验后台提交的配置:整数项必须为正整数;sec_uid 若填须 MS4w 开头。"""
    for spec in EDITABLE_SETTINGS:
        if spec["key"] not in payload:
            continue
        val = payload[spec["key"]]
        if spec["type"] == "int":
            try:
                if int(val) <= 0:
                    return False, f"{spec['label']} 必须是正整数"
            except (TypeError, ValueError):
                return False, f"{spec['label']} 必须是整数"
        if spec["key"] == "author_sec_uid" and val and not str(val).startswith("MS4w"):
            return False, "sec_uid 应以 MS4w 开头（可用校验按钮从主页链接解析）"
    return True, ""
