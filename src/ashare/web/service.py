"""看板数据装配：把 db 行组装成前端需要的视图模型。

纯数据变换（除时间格式化外不含 IO），便于单测。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from ..db import Database
from ..models import Stage

_BJ_TZ = timezone(timedelta(hours=8))
# 阶段中文名，供看板展示
STAGE_LABELS = {
    "discover": "发现", "download": "下载", "extract_audio": "抽音轨",
    "transcribe": "转写", "extract": "抽取观点", "notify": "推送",
}
_ALL_STAGES = ["discover", "download", "extract_audio", "transcribe", "extract", "notify"]


def fmt_time(ts: int) -> str:
    """Unix 秒转东八区可读字符串；0 显示为 '-'。"""
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, _BJ_TZ).strftime("%Y-%m-%d %H:%M")


def _stage_list(stage_rows: dict) -> list[dict]:
    """把某视频的阶段状态整理为有序列表（含未开始的阶段）。"""
    out = []
    for s in _ALL_STAGES:
        row = stage_rows.get(s, {})
        out.append({
            "stage": s,
            "label": STAGE_LABELS[s],
            "status": row.get("status", "pending"),
            "error": row.get("error", ""),
            "finished_at": fmt_time(row.get("finished_at") or 0),
        })
    return out


def video_summary(db: Database, aweme_id: str) -> dict:
    """单个视频的列表级摘要（用于列表页）。"""
    v = db.get_video(aweme_id)
    stages = db.get_stages(aweme_id)
    analysis = db.get_analysis(aweme_id)
    return {
        "aweme_id": v.aweme_id,
        "title": v.title or "(待分析/无标题)",
        "author_name": v.author_name,
        "video_url": v.video_url,
        "publish_time": fmt_time(v.publish_time),
        "overall_status": v.overall_status.value,
        "stages": _stage_list(stages),
        "opinion_count": len(analysis.opinions) if analysis else 0,
        "sectors": analysis.sectors if analysis else [],
    }


def dashboard_data(db: Database) -> dict:
    """列表页数据：所有视频摘要 + 总体统计。"""
    videos = [video_summary(db, v.aweme_id) for v in db.list_videos()]
    stats = _aggregate_stats(videos)
    return {"videos": videos, "stats": stats}


def _aggregate_stats(videos: list[dict]) -> dict:
    """汇总各状态视频数量与观点总数（纯统计）。"""
    counts = {"success": 0, "running": 0, "failed": 0, "pending": 0}
    opinions = 0
    for v in videos:
        counts[v["overall_status"]] = counts.get(v["overall_status"], 0) + 1
        opinions += v["opinion_count"]
    return {"total": len(videos), "opinions": opinions, **counts}


def video_detail(db: Database, aweme_id: str) -> dict | None:
    """详情页数据：摘要 + 转写全文 + 逐条观点 + AI 输入输出。"""
    v = db.get_video(aweme_id)
    if not v:
        return None
    base = video_summary(db, aweme_id)
    transcript = db.get_transcript(aweme_id)
    analysis = db.get_analysis(aweme_id)
    base.update({
        "transcript": transcript.text if transcript else "",
        "audio_seconds": transcript.audio_seconds if transcript else 0,
        "summary": analysis.summary if analysis else "",
        "analysis_time": fmt_time(analysis.created_at) if analysis else "-",
        "llm_model": analysis.llm_model if analysis else "",
        "prompt_input": analysis.prompt_input if analysis else "",
        "raw_output": analysis.raw_output if analysis else "",
        "opinions": [_opinion_dict(o) for o in (analysis.opinions if analysis else [])],
    })
    return base


def _opinion_dict(o) -> dict:
    return {
        "target_name": o.target_name, "target_code": o.target_code,
        "target_type": o.target_type.value, "stance": o.stance.value,
        "confidence": o.confidence, "logic": o.logic, "quote": o.quote,
    }
