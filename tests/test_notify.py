"""推送层单测：消息构造、拆条、发送（注入 poster）、工厂、Noop/Fake。不联网。"""

from ashare.config import TelegramConfig
from ashare.models import Analysis, Opinion, Stance, TargetType, Video
from ashare.notify.base import FakeNotifier, NoopNotifier, make_notifier
from ashare.notify.telegram import TelegramNotifier, build_message, split_message
from ashare.result import Err, Ok


def _video():
    return Video("v1", "s", "看好半导体", "https://www.douyin.com/video/v1",
                 publish_time=1704135845, author_name="创业魔法师")


def _analysis():
    return Analysis("v1", "m", "in", "out", summary="整体看多半导体", sectors=["半导体", "AI"],
                    opinions=[Opinion("中芯国际", TargetType.STOCK, Stance.BULLISH, "国产替代",
                                      target_code="688981")])


def test_build_message_contains_all_parts():
    msg = build_message(_video(), "我看好半导体国产替代", _analysis())
    assert "看好半导体" in msg                 # 标题
    assert "创业魔法师" in msg                  # 作者
    assert "2024-01-02 03:04" in msg            # 发布时间
    assert 'href="https://www.douyin.com/video/v1"' in msg  # 原链接
    assert "整体看多半导体" in msg              # 摘要
    assert "半导体、AI" in msg                  # 板块
    assert "中芯国际" in msg and "688981" in msg  # 观点
    assert "🔴 [看多]" in msg                    # 多空图标
    assert "我看好半导体国产替代" in msg         # 原文


def test_build_message_html_escape():
    v = _video(); v.title = "A & B <test>"
    msg = build_message(v, "x", None)
    assert "A &amp; B &lt;test&gt;" in msg
    assert "未抽取到明确" in msg                 # 无 analysis 时的提示


def test_split_message_short_single():
    assert split_message("hello", limit=100) == ["hello"]


def test_split_message_long_splits():
    text = "\n".join(["行%d" % i for i in range(500)])
    parts = split_message(text, limit=100)
    assert len(parts) > 1
    assert all(len(p) <= 100 for p in parts)


def test_split_message_hard_split_long_line():
    parts = split_message("x" * 250, limit=100)
    assert len(parts) == 3 and all(len(p) <= 100 for p in parts)


def test_telegram_notifier_sends_all_chunks():
    posted = []

    def poster(url, payload):
        posted.append(payload["text"])
        return {"ok": True}

    n = TelegramNotifier("tok", "-100", poster=poster, sleep=lambda s: None)
    long = "\n".join(["x" * 100 for _ in range(100)])   # ~10000 字符，超 4000
    assert isinstance(n.send(long), Ok)
    assert len(posted) > 1                       # 拆成多条都发了
    assert all(len(p) <= 4000 for p in posted)


def test_telegram_notifier_retries_then_fails():
    calls = {"n": 0}

    def poster(url, payload):
        calls["n"] += 1
        return {"ok": False, "description": "bad"}

    n = TelegramNotifier("tok", "-100", poster=poster, sleep=lambda s: None)
    r = n.send("hi")
    assert isinstance(r, Err)
    assert calls["n"] == 2                        # 重试 1 次


def test_make_notifier_enabled_vs_noop():
    on = make_notifier(TelegramConfig(enabled=True, bot_token="t", chat_id="-1"))
    assert isinstance(on, TelegramNotifier)
    # 未启用 / 缺 token → Noop
    assert isinstance(make_notifier(TelegramConfig(enabled=False, bot_token="t", chat_id="-1")), NoopNotifier)
    assert isinstance(make_notifier(TelegramConfig(enabled=True, bot_token="", chat_id="-1")), NoopNotifier)


def test_fake_and_noop():
    f = FakeNotifier(); f.send("a"); f.send("b")
    assert f.sent == ["a", "b"]
    assert isinstance(NoopNotifier().send("x"), Ok)
    assert NoopNotifier().enabled() is False
