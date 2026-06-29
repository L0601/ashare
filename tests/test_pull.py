"""PullManager 单测：单飞、状态流转、错误捕获。

用 threading.Event 控制 fake run_fn 的时序，确保不依赖 wall clock / sleep。
"""

import threading
import time

from ashare.web.pull import PullManager


def _wait(cond, timeout=2.0):
    """轮询等待条件成立，避免依赖固定 sleep。"""
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_trigger_runs_and_records_result():
    pm = PullManager(run_fn=lambda: {"new": 2, "processed": 2})
    assert pm.trigger() is True
    assert _wait(lambda: not pm.status()["running"])
    st = pm.status()
    assert st["last_result"] == {"new": 2, "processed": 2}
    assert st["last_error"] is None
    assert st["finished_at"] is not None


def test_single_flight_second_trigger_rejected():
    gate = threading.Event()

    def slow():
        gate.wait(timeout=2)      # 卡住，模拟长任务
        return {"new": 0, "processed": 0}

    pm = PullManager(run_fn=slow)
    assert pm.trigger() is True
    assert _wait(lambda: pm.status()["running"])   # 已在跑
    # 运行中再次 trigger 应被拒（单飞）
    assert pm.trigger() is False
    gate.set()                                     # 放行让第一个结束
    assert _wait(lambda: not pm.status()["running"])
    # 结束后可再次触发
    assert pm.trigger() is True
    assert _wait(lambda: not pm.status()["running"])


def test_error_captured_and_running_reset():
    def boom():
        raise RuntimeError("抖音风控拦截")

    pm = PullManager(run_fn=boom)
    assert pm.trigger() is True
    assert _wait(lambda: not pm.status()["running"])
    st = pm.status()
    assert st["running"] is False        # 出错也复位
    assert "抖音风控拦截" in st["last_error"]


def test_initial_status_idle():
    pm = PullManager(run_fn=lambda: {})
    st = pm.status()
    assert st["running"] is False
    assert st["last_result"] is None
    assert st["started_at"] is None
