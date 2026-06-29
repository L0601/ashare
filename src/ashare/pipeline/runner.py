"""流水线状态机：逐视频跑 下载→抽音轨→转写→抽取 四阶段。

设计要点：
- 依赖（db / 抓取 / 下载 / 转写 / 抽取 / 限速）全部注入，核心流转逻辑可用 fake 单测。
- 幂等可续跑：阶段已 SUCCESS 则跳过；中途失败下次从失败阶段继续。
- 副作用集中在各 _stage 方法；状态流转集中在 _run_stage。
- 限速 pace() 在每次网络请求前调用，错峰防风控。
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Callable

from ..asr.base import Transcriber, make_transcriber
from ..config import AppConfig
from ..db import Database
from ..douyin.base import DouyinClient
from ..llm.extractor import LLMExtractor
from ..media.audio import extract_audio, probe_duration
from ..models import Stage, Status, Transcript, Video
from ..result import Err, Ok, Result


class Pipeline:
    def __init__(
        self,
        cfg: AppConfig,
        db: Database,
        client: DouyinClient,
        transcriber: Transcriber,
        extractor: LLMExtractor,
        *,
        download_fn: Callable = None,
        extract_audio_fn: Callable = extract_audio,
        probe_fn: Callable = probe_duration,
        pace: Callable[[], None] | None = None,
    ):
        self.cfg = cfg
        self.db = db
        self.client = client
        self.transcriber = transcriber
        self.extractor = extractor
        self._download_fn = download_fn or _default_download
        self._extract_audio_fn = extract_audio_fn
        self._probe_fn = probe_fn
        self._pace = pace or _make_pacer(cfg)

    # ---------- 对外入口 ----------
    def discover(self) -> Result[list[Video], str]:
        """抓取最新视频，入库新视频并标记 discover 成功。返回本次新发现的视频。"""
        self._pace()
        res = self.client.list_latest(self.cfg.douyin.author_sec_uid, self.cfg.douyin.latest_count)
        if not res.is_ok():
            return res
        known = self.db.known_aweme_ids()
        new_videos = [v for v in res.value if v.aweme_id not in known]
        for v in res.value:
            self.db.upsert_video(v)
            self.db.set_stage(v.aweme_id, Stage.DISCOVER, Status.SUCCESS)
        return Ok(new_videos)

    def run_once(self, limit: int | None = None) -> dict:
        """一轮：发现新视频 + 处理未完成视频。limit 限制本轮处理条数（取最新的若干条）。"""
        poll_id = self.db.start_poll()
        discovered = self.discover()
        if not discovered.is_ok():
            self.db.finish_poll(poll_id, 0, "failed", str(discovered.error))
            return {"new": 0, "processed": 0, "error": discovered.error}
        pending = self._pending_videos()
        if limit is not None:
            pending = pending[:limit]
        processed = sum(self.process_video(v) for v in pending)
        self.db.finish_poll(poll_id, len(discovered.value), "success")
        return {"new": len(discovered.value), "processed": processed}

    def process_video(self, video: Video) -> bool:
        """跑完单个视频的四阶段；任一阶段失败即停并标记视频失败。"""
        self.db.set_video_status(video.aweme_id, Status.RUNNING)
        stages = [
            (Stage.DOWNLOAD, lambda: self._download(video)),
            (Stage.EXTRACT_AUDIO, lambda: self._do_extract_audio(video)),
            (Stage.TRANSCRIBE, lambda: self._transcribe(video)),
            (Stage.EXTRACT, lambda: self._extract(video)),
        ]
        for stage, work in stages:
            if not self._run_stage(video.aweme_id, stage, work):
                self.db.set_video_status(video.aweme_id, Status.FAILED)
                return False
        self.db.set_video_status(video.aweme_id, Status.SUCCESS)
        return True

    # ---------- 状态机核心 ----------
    def _run_stage(self, aweme_id: str, stage: Stage, work: Callable[[], Result]) -> bool:
        """执行一个阶段并落状态。已成功则幂等跳过。"""
        if self.db.get_stage_status(aweme_id, stage) == Status.SUCCESS:
            return True
        self.db.set_stage(aweme_id, stage, Status.RUNNING)
        result = work()
        if result.is_ok():
            self.db.set_stage(aweme_id, stage, Status.SUCCESS)
            return True
        self.db.set_stage(aweme_id, stage, Status.FAILED, error=str(result.error))
        return False

    def _pending_videos(self) -> list[Video]:
        """挑出尚未成功的视频（含失败的，便于重试续跑）。"""
        return [v for v in self.db.list_videos() if v.overall_status != Status.SUCCESS]

    # ---------- 各阶段副作用 ----------
    def _download(self, video: Video) -> Result[Path, str]:
        url_res = self.client.get_download_url(video.aweme_id)
        url = url_res.value if url_res.is_ok() else video.download_url
        if not url:
            return Err(f"无下载地址：{url_res.error if not url_res.is_ok() else ''}")
        self._pace()
        return self._download_fn(
            url, self._video_path(video.aweme_id),
            user_agent=self.cfg.douyin.user_agent, cookie=self.cfg.douyin.cookie,
        )

    def _do_extract_audio(self, video: Video) -> Result[Path, str]:
        return self._extract_audio_fn(
            self._video_path(video.aweme_id), self._audio_path(video.aweme_id)
        )

    def _transcribe(self, video: Video) -> Result[Transcript, str]:
        res = self.transcriber.transcribe(self._audio_path(video.aweme_id))
        if not res.is_ok():
            return res
        duration = self._probe_fn(self._video_path(video.aweme_id))
        t = Transcript(
            aweme_id=video.aweme_id, text=res.value.text,
            asr_engine=res.value.engine, audio_seconds=duration,
        )
        self.db.save_transcript(t)
        self._transcript_path(video.aweme_id).write_text(t.text, encoding="utf-8")
        return Ok(t)

    def _extract(self, video: Video) -> Result[None, str]:
        t = self.db.get_transcript(video.aweme_id)
        if not t or not t.text.strip():
            return Err("无可用转写文本")
        res = self.extractor.extract(video.aweme_id, video.title, t.text)
        if not res.is_ok():
            return res
        self.db.save_analysis(res.value)
        # 视频无原生文案标题时，用 LLM 生成的标题回填，便于看板展示
        if not video.title and res.value.title:
            self.db.set_video_title(video.aweme_id, res.value.title)
        self._analysis_path(video.aweme_id).write_text(
            res.value.raw_output, encoding="utf-8"
        )
        return Ok(None)

    # ---------- 路径 ----------
    def _dir(self, aweme_id: str) -> Path:
        d = self.cfg.videos_dir / aweme_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _video_path(self, aweme_id: str) -> Path:
        return self._dir(aweme_id) / "video.mp4"

    def _audio_path(self, aweme_id: str) -> Path:
        return self._dir(aweme_id) / "audio.wav"

    def _transcript_path(self, aweme_id: str) -> Path:
        return self._dir(aweme_id) / "transcript.txt"

    def _analysis_path(self, aweme_id: str) -> Path:
        return self._dir(aweme_id) / "analysis.json"


def _default_download(url, dest, **kw):
    # 延迟导入，避免无谓依赖
    from ..douyin.downloader import download_video

    return download_video(url, dest, **kw)


def _make_pacer(cfg: AppConfig) -> Callable[[], None]:
    """构造限速器：基础间隔 + 随机抖动，错峰防风控。"""
    base = cfg.schedule.request_min_seconds
    jitter = cfg.schedule.request_jitter_seconds

    def pace() -> None:
        time.sleep(base + random.uniform(0, jitter))

    return pace


def build_pipeline(cfg: AppConfig, db: Database | None = None) -> Pipeline:
    """按配置装配真实流水线（生产用）。"""
    from ..douyin.client import F2DouyinClient

    database = db or Database(cfg.db_path)
    return Pipeline(
        cfg=cfg,
        db=database,
        client=F2DouyinClient(cfg),
        transcriber=make_transcriber(cfg.asr),
        extractor=LLMExtractor(cfg.llm),
    )
