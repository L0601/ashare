import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ashare.config import AppConfig, DouyinConfig, LLMConfig, ScheduleConfig
from ashare.pipeline import scheduler


class FakeBlockingScheduler:
    def __init__(self, timezone):
        self.timezone = timezone
        self.jobs = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append((func, trigger, kwargs))

    def start(self):
        return None


class FakePipeline:
    def __init__(self):
        self.calls = 0

    def run_once(self):
        self.calls += 1
        return {"new": 0, "processed": 0}


def _cfg():
    return AppConfig(
        douyin=DouyinConfig(cookie="ck", author_sec_uid="sec1"),
        llm=LLMConfig(base_url="http://x", api_key="k", model="m"),
        schedule=ScheduleConfig(poll_interval_minutes=90),
    )


def test_scheduler_skips_poll_during_beijing_quiet_hours(monkeypatch, caplog):
    pipe = FakePipeline()
    caplog.set_level(logging.INFO, logger="ashare.scheduler")
    monkeypatch.setattr(scheduler, "BlockingScheduler", FakeBlockingScheduler)
    monkeypatch.setattr(scheduler, "build_pipeline", lambda cfg: pipe)
    monkeypatch.setattr(
        scheduler,
        "now_beijing",
        lambda: datetime(2026, 7, 6, 1, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        raising=False,
    )

    scheduler.run_scheduler(_cfg())

    assert pipe.calls == 0
    assert "北京时间 01:00-08:00 暂停轮询" in caplog.text


def test_scheduler_runs_poll_after_beijing_quiet_hours(monkeypatch):
    pipe = FakePipeline()
    monkeypatch.setattr(scheduler, "BlockingScheduler", FakeBlockingScheduler)
    monkeypatch.setattr(scheduler, "build_pipeline", lambda cfg: pipe)
    monkeypatch.setattr(
        scheduler,
        "now_beijing",
        lambda: datetime(2026, 7, 6, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        raising=False,
    )

    scheduler.run_scheduler(_cfg())

    assert pipe.calls == 1
