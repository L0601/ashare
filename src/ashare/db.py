"""SQLite 持久化层。

设计：
- schema 与 DAO 集中在此，其它模块只调方法，不写裸 SQL。
- 时钟通过构造函数注入（clock），核心逻辑不直接读 time.time，便于单测。
- 一视频一阶段一行（stage_runs UNIQUE(aweme_id,stage)），支撑各流程状态与续跑。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional

from .models import (
    Analysis,
    Opinion,
    Stage,
    Stance,
    Status,
    TargetType,
    Transcript,
    Video,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    aweme_id TEXT PRIMARY KEY,
    author_sec_uid TEXT NOT NULL,
    title TEXT NOT NULL,
    video_url TEXT NOT NULL,
    publish_time INTEGER NOT NULL,
    author_name TEXT DEFAULT '',
    cover_url TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    overall_status TEXT NOT NULL DEFAULT 'pending',
    discovered_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS stage_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aweme_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT DEFAULT '',
    started_at INTEGER,
    finished_at INTEGER,
    attempts INTEGER DEFAULT 0,
    UNIQUE(aweme_id, stage)
);
CREATE TABLE IF NOT EXISTS transcripts (
    aweme_id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    asr_engine TEXT NOT NULL,
    audio_seconds REAL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    aweme_id TEXT PRIMARY KEY,
    llm_model TEXT NOT NULL,
    prompt_input TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    summary TEXT DEFAULT '',
    sectors TEXT DEFAULT '[]',
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS opinions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aweme_id TEXT NOT NULL,
    target_name TEXT NOT NULL,
    target_code TEXT DEFAULT '',
    target_type TEXT NOT NULL,
    stance TEXT NOT NULL,
    confidence REAL DEFAULT 0,
    logic TEXT DEFAULT '',
    quote TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS poll_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    new_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    error TEXT DEFAULT ''
);
-- 运行时可改配置（后台编辑），覆盖文件默认值。不随"清空数据"被清除。
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    """SQLite DAO。传 ":memory:" 可用于单测。"""

    def __init__(self, path: str | Path, clock: Callable[[], int] | None = None):
        self._clock = clock or (lambda: int(time.time()))
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        # WAL + busy_timeout：允许"看板每2s读"与"后台拉取写"并发，避免 database is locked
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """轻量迁移：给老库补上后加的列（IF NOT EXISTS 建表不会改既有表结构）。"""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(videos)")}
        if "author_name" not in cols:
            self.conn.execute("ALTER TABLE videos ADD COLUMN author_name TEXT DEFAULT ''")

    def close(self) -> None:
        self.conn.close()

    # 所有业务表，供重置使用
    _TABLES = ("videos", "stage_runs", "transcripts", "analyses", "opinions", "poll_runs")

    def reset(self) -> None:
        """删除所有表并重建空表（清空全部数据）。"""
        for t in self._TABLES:
            self.conn.execute(f"DROP TABLE IF EXISTS {t}")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- videos ----------
    def upsert_video(self, v: Video) -> None:
        """插入或更新视频元数据（已存在则不覆盖 overall_status）。"""
        now = self._clock()
        self.conn.execute(
            """INSERT INTO videos (aweme_id, author_sec_uid, title, video_url,
                   publish_time, author_name, cover_url, duration_ms, overall_status,
                   discovered_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(aweme_id) DO UPDATE SET
                   -- 抖音 desc 为空时不要覆盖已有标题（如 LLM 回填的标题），否则重复 discover 会抹掉它
                   title=CASE WHEN excluded.title <> '' THEN excluded.title ELSE videos.title END,
                   author_name=CASE WHEN excluded.author_name <> '' THEN excluded.author_name ELSE videos.author_name END,
                   video_url=excluded.video_url,
                   cover_url=excluded.cover_url, duration_ms=excluded.duration_ms,
                   updated_at=excluded.updated_at""",
            (v.aweme_id, v.author_sec_uid, v.title, v.video_url, v.publish_time,
             v.author_name, v.cover_url, v.duration_ms, v.overall_status.value, now, now),
        )
        self.conn.commit()

    def set_video_title(self, aweme_id: str, title: str) -> None:
        """回填标题（用于原生文案为空、由 LLM 生成标题的情况）。"""
        self.conn.execute(
            "UPDATE videos SET title=?, updated_at=? WHERE aweme_id=?",
            (title, self._clock(), aweme_id),
        )
        self.conn.commit()

    def set_video_status(self, aweme_id: str, status: Status) -> None:
        self.conn.execute(
            "UPDATE videos SET overall_status=?, updated_at=? WHERE aweme_id=?",
            (status.value, self._clock(), aweme_id),
        )
        self.conn.commit()

    def get_video(self, aweme_id: str) -> Optional[Video]:
        row = self.conn.execute(
            "SELECT * FROM videos WHERE aweme_id=?", (aweme_id,)
        ).fetchone()
        return _row_to_video(row) if row else None

    def list_videos(self) -> list[Video]:
        rows = self.conn.execute(
            "SELECT * FROM videos ORDER BY publish_time DESC"
        ).fetchall()
        return [_row_to_video(r) for r in rows]

    def known_aweme_ids(self) -> set[str]:
        rows = self.conn.execute("SELECT aweme_id FROM videos").fetchall()
        return {r["aweme_id"] for r in rows}

    # ---------- stage_runs ----------
    def set_stage(
        self, aweme_id: str, stage: Stage, status: Status, error: str = ""
    ) -> None:
        """记录某视频某阶段的状态；running 时刷新 started_at 并 +1 attempts。"""
        now = self._clock()
        started = now if status == Status.RUNNING else None
        finished = now if status in (Status.SUCCESS, Status.FAILED, Status.SKIPPED) else None
        self.conn.execute(
            """INSERT INTO stage_runs (aweme_id, stage, status, error,
                   started_at, finished_at, attempts)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(aweme_id, stage) DO UPDATE SET
                   status=excluded.status, error=excluded.error,
                   started_at=COALESCE(excluded.started_at, stage_runs.started_at),
                   finished_at=excluded.finished_at,
                   attempts=stage_runs.attempts + (CASE WHEN excluded.status='running' THEN 1 ELSE 0 END)""",
            (aweme_id, stage.value, status.value, error, started, finished,
             1 if status == Status.RUNNING else 0),
        )
        self.conn.commit()

    def get_stage_status(self, aweme_id: str, stage: Stage) -> Status:
        row = self.conn.execute(
            "SELECT status FROM stage_runs WHERE aweme_id=? AND stage=?",
            (aweme_id, stage.value),
        ).fetchone()
        return Status(row["status"]) if row else Status.PENDING

    def get_stages(self, aweme_id: str) -> dict[str, dict]:
        """返回该视频所有阶段的状态明细（供看板展示）。"""
        rows = self.conn.execute(
            "SELECT * FROM stage_runs WHERE aweme_id=?", (aweme_id,)
        ).fetchall()
        return {r["stage"]: dict(r) for r in rows}

    # ---------- transcripts ----------
    def save_transcript(self, t: Transcript) -> None:
        self.conn.execute(
            """INSERT INTO transcripts (aweme_id, text, asr_engine, audio_seconds, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(aweme_id) DO UPDATE SET
                   text=excluded.text, asr_engine=excluded.asr_engine,
                   audio_seconds=excluded.audio_seconds, created_at=excluded.created_at""",
            (t.aweme_id, t.text, t.asr_engine, t.audio_seconds, self._clock()),
        )
        self.conn.commit()

    def get_transcript(self, aweme_id: str) -> Optional[Transcript]:
        row = self.conn.execute(
            "SELECT * FROM transcripts WHERE aweme_id=?", (aweme_id,)
        ).fetchone()
        if not row:
            return None
        return Transcript(
            aweme_id=row["aweme_id"], text=row["text"], asr_engine=row["asr_engine"],
            audio_seconds=row["audio_seconds"], created_at=row["created_at"],
        )

    # ---------- analyses + opinions ----------
    def save_analysis(self, a: Analysis) -> None:
        """保存分析结果；先清空该视频旧观点再重插，保证幂等。"""
        now = self._clock()
        self.conn.execute(
            """INSERT INTO analyses (aweme_id, llm_model, prompt_input, raw_output,
                   summary, sectors, created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(aweme_id) DO UPDATE SET
                   llm_model=excluded.llm_model, prompt_input=excluded.prompt_input,
                   raw_output=excluded.raw_output, summary=excluded.summary,
                   sectors=excluded.sectors, created_at=excluded.created_at""",
            (a.aweme_id, a.llm_model, a.prompt_input, a.raw_output, a.summary,
             json.dumps(a.sectors, ensure_ascii=False), now),
        )
        self.conn.execute("DELETE FROM opinions WHERE aweme_id=?", (a.aweme_id,))
        for op in a.opinions:
            self.conn.execute(
                """INSERT INTO opinions (aweme_id, target_name, target_code,
                       target_type, stance, confidence, logic, quote)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (a.aweme_id, op.target_name, op.target_code, op.target_type.value,
                 op.stance.value, op.confidence, op.logic, op.quote),
            )
        self.conn.commit()

    def get_analysis(self, aweme_id: str) -> Optional[Analysis]:
        row = self.conn.execute(
            "SELECT * FROM analyses WHERE aweme_id=?", (aweme_id,)
        ).fetchone()
        if not row:
            return None
        return Analysis(
            aweme_id=row["aweme_id"], llm_model=row["llm_model"],
            prompt_input=row["prompt_input"], raw_output=row["raw_output"],
            summary=row["summary"], sectors=json.loads(row["sectors"] or "[]"),
            opinions=self.get_opinions(aweme_id), created_at=row["created_at"],
        )

    def get_opinions(self, aweme_id: str) -> list[Opinion]:
        rows = self.conn.execute(
            "SELECT * FROM opinions WHERE aweme_id=? ORDER BY id", (aweme_id,)
        ).fetchall()
        return [
            Opinion(
                id=r["id"], aweme_id=r["aweme_id"], target_name=r["target_name"],
                target_code=r["target_code"], target_type=TargetType(r["target_type"]),
                stance=Stance(r["stance"]), confidence=r["confidence"],
                logic=r["logic"], quote=r["quote"],
            )
            for r in rows
        ]

    # ---------- poll_runs ----------
    def start_poll(self) -> int:
        cur = self.conn.execute(
            "INSERT INTO poll_runs (started_at, status) VALUES (?, 'running')",
            (self._clock(),),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_poll(self, poll_id: int, new_count: int, status: str, error: str = "") -> None:
        self.conn.execute(
            "UPDATE poll_runs SET finished_at=?, new_count=?, status=?, error=? WHERE id=?",
            (self._clock(), new_count, status, error, poll_id),
        )
        self.conn.commit()

    # ---------- settings（运行时配置，不随 reset 清除）----------
    def get_settings(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def set_settings(self, items: dict[str, str]) -> None:
        """批量写入配置；值为 None 的键跳过（不覆盖）。"""
        for k, v in items.items():
            if v is None:
                continue
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, str(v)),
            )
        self.conn.commit()

    # ---------- 单条数据删除 ----------
    def delete_video(self, aweme_id: str) -> None:
        """删除该视频在所有数据表中的记录（不含 settings/poll_runs 全局表）。"""
        for t in ("videos", "stage_runs", "transcripts", "analyses", "opinions"):
            self.conn.execute(f"DELETE FROM {t} WHERE aweme_id=?", (aweme_id,))
        self.conn.commit()


def _row_to_video(row: sqlite3.Row) -> Video:
    return Video(
        aweme_id=row["aweme_id"], author_sec_uid=row["author_sec_uid"],
        title=row["title"], video_url=row["video_url"],
        publish_time=row["publish_time"], author_name=row["author_name"],
        cover_url=row["cover_url"],
        duration_ms=row["duration_ms"], overall_status=Status(row["overall_status"]),
        discovered_at=row["discovered_at"], updated_at=row["updated_at"],
    )
