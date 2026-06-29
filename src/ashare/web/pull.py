"""后台拉取管理器：在独立线程跑一次 run_once，单飞 + 状态可查。

设计：
- run_fn 注入（生产里是 build_pipeline(cfg).run_once(limit=N)），便于单测传 fake。
- 单飞：同一时刻只允许一个拉取在跑，防止重复处理与抖音请求过频触发风控。
- 拉取耗时（下载+ASR+LLM），放后台线程，HTTP 立即返回；看板靠轮询看进度。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class PullManager:
    def __init__(self, run_fn: Callable[[], dict]):
        self._run_fn = run_fn
        self._lock = threading.Lock()       # 抢到锁=拿到唯一执行权
        self._running = False
        self._started_at: Optional[int] = None
        self._finished_at: Optional[int] = None
        self._last_result: Optional[dict] = None
        self._last_error: Optional[str] = None

    def trigger(self) -> bool:
        """发起一次后台拉取。已有任务在跑则返回 False（单飞）。"""
        if not self._lock.acquire(blocking=False):
            return False
        self._running = True
        self._started_at = int(time.time())
        self._finished_at = None
        self._last_error = None
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            self._last_result = self._run_fn()
        except Exception as e:  # 拉取失败按值记录，不让线程崩
            self._last_error = str(e)
        finally:
            self._running = False
            self._finished_at = int(time.time())
            self._lock.release()

    def status(self) -> dict:
        """当前状态，供前端展示与按钮禁用。"""
        return {
            "running": self._running,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "last_result": self._last_result,
            "last_error": self._last_error,
        }
