"""定时轮询调度：按配置间隔跑 run_once，错峰防风控。

调度本身是胶水代码：启动时立即跑一轮，之后按 poll_interval_minutes 周期触发。
真正的防风控间隔/抖动在 Pipeline 内（pace），此处只负责"多久来一次"。
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from ..config import AppConfig
from .runner import build_pipeline

logger = logging.getLogger("ashare.scheduler")
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def now_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def in_quiet_hours(now: datetime | None = None) -> bool:
    """北京时间 01:00 <= now < 08:00 时暂停自动轮询。"""
    current = now or now_beijing()
    beijing_now = current.astimezone(BEIJING_TZ)
    return 1 <= beijing_now.hour < 8


def run_scheduler(cfg: AppConfig) -> None:
    pipeline = build_pipeline(cfg)

    def job() -> None:
        if in_quiet_hours():
            logger.info(
                "北京时间 01:00-08:00 暂停轮询，当前时间：%s",
                now_beijing().strftime("%H:%M:%S"),
            )
            return
        logger.info("开始一轮轮询")
        stats = pipeline.run_once()
        logger.info("本轮完成：%s", stats)

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    # 注意：不要传 next_run_time=None —— 那会把任务加成"暂停"状态、永不触发。
    # 首轮由下面的 job() 手动跑；间隔任务正常在 N 分钟后开始周期触发。
    scheduler.add_job(
        job, "interval",
        minutes=cfg.schedule.poll_interval_minutes,
        id="poll",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    logger.info("调度启动，间隔 %d 分钟", cfg.schedule.poll_interval_minutes)
    job()  # 启动即跑一轮，之后按间隔
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度停止")
