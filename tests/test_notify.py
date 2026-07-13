"""推送层单测：消息构造、拆条、发送（注入 poster）、工厂、Noop/Fake。不联网。"""

import logging

import pytest

from ashare.config import TelegramConfig
from ashare.models import Analysis, Opinion, Stance, TargetType, Video
from ashare.notify.base import FakeNotifier, NoopNotifier, make_notifier
from ashare.notify.telegram import TelegramNotifier, _httpx_poster, build_message, split_message
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


def test_telegram_notifier_renders_notification_payload():
    from ashare.notify.base import NotificationPayload

    posted = []

    def poster(url, payload):
        posted.append(payload["text"])
        return {"ok": True}

    notifier = TelegramNotifier("tok", "-100", poster=poster, sleep=lambda _: None)

    result = notifier.send(NotificationPayload(_video(), "口播原文", _analysis()))

    assert isinstance(result, Ok)
    assert len(posted) == 1
    assert "<b>看好半导体</b>" in posted[0]
    assert "口播原文" in posted[0]


def test_telegram_notifier_retries_then_fails():
    calls = {"n": 0}

    def poster(url, payload):
        calls["n"] += 1
        return {"ok": False, "description": "bad"}

    n = TelegramNotifier("tok", "-100", poster=poster, sleep=lambda s: None)
    r = n.send("hi")
    assert isinstance(r, Err)
    assert calls["n"] == 2                        # 重试 1 次


def test_httpx_poster_suppresses_token_url_logs(monkeypatch):
    httpx_logger = logging.getLogger("httpx")
    old_level = httpx_logger.level
    httpx_logger.setLevel(logging.INFO)

    def fake_post(url, json, timeout):
        assert httpx_logger.getEffectiveLevel() >= logging.WARNING

        class Resp:
            def json(self):
                return {"ok": True}

        return Resp()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)

    assert _httpx_poster("https://api.telegram.org/botSECRET/sendMessage", {}) == {"ok": True}
    assert httpx_logger.level == logging.INFO
    httpx_logger.setLevel(old_level)


def test_make_notifier_enabled_vs_noop():
    on = make_notifier(TelegramConfig(enabled=True, bot_token="t", chat_id="-1"))
    assert isinstance(on, TelegramNotifier)
    # 未启用 / 缺 token → Noop
    assert isinstance(make_notifier(TelegramConfig(enabled=False, bot_token="t", chat_id="-1")), NoopNotifier)
    assert isinstance(make_notifier(TelegramConfig(enabled=True, bot_token="", chat_id="-1")), NoopNotifier)


def test_make_notifiers_builds_telegram_and_wecom_channels():
    from ashare.config import AppConfig, DouyinConfig, LLMConfig, WeComConfig
    from ashare.notify.base import Notifier, make_notifiers

    cfg = AppConfig(
        douyin=DouyinConfig(),
        llm=LLMConfig(base_url="http://llm", api_key="k", model="m"),
        telegram=TelegramConfig(enabled=True, bot_token="tok", chat_id="-1"),
        wecom=WeComConfig(
            enabled=True,
            webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
        ),
    )

    notifiers = make_notifiers(cfg)

    assert [notifier.channel for notifier in notifiers] == ["telegram", "wecom"]
    assert all(isinstance(notifier, Notifier) for notifier in notifiers)


def test_make_notifiers_rejects_invalid_enabled_wecom_url_without_echoing_it():
    from ashare.config import AppConfig, DouyinConfig, LLMConfig, WeComConfig
    from ashare.notify.base import make_notifiers

    cfg = AppConfig(
        douyin=DouyinConfig(),
        llm=LLMConfig(base_url="http://llm", api_key="k", model="m"),
        wecom=WeComConfig(enabled=True, webhook_url="http://example.com/?key=secret-key"),
    )

    with pytest.raises(ValueError) as raised:
        make_notifiers(cfg)

    assert "企业微信 webhook_url 格式不合法" in str(raised.value)
    assert "secret-key" not in str(raised.value)


def test_fake_and_noop():
    f = FakeNotifier(); f.send("a"); f.send("b")
    assert f.sent == ["a", "b"]
    assert isinstance(NoopNotifier().send("x"), Ok)
    assert NoopNotifier().enabled() is False


def test_notify_package_exports_wecom_public_api():
    import ashare.notify as notify

    assert notify.NotificationPayload is not None
    assert notify.WeComNotifier is not None
    assert notify.make_notifiers is not None


def test_build_wecom_message_contains_analysis_and_transcript():
    from ashare.notify.base import NotificationPayload
    from ashare.notify.wecom import build_message as build_wecom_message

    payload = NotificationPayload(_video(), "我看好半导体国产替代", _analysis())

    msg = build_wecom_message(payload)

    assert "### 🎬 看好半导体" in msg
    assert "创业魔法师" in msg
    assert "[原视频](https://www.douyin.com/video/v1)" in msg
    assert "整体看多半导体" in msg
    assert "半导体、AI" in msg
    assert "中芯国际（688981）" in msg
    assert "🔴 [看多]" in msg
    assert "我看好半导体国产替代" in msg


def test_build_wecom_message_escapes_dynamic_markdown_and_html():
    from ashare.notify.base import NotificationPayload
    from ashare.notify.wecom import build_message as build_wecom_message

    video = _video()
    video.title = '突破 <font color="warning">危险</font>\n# 伪标题'

    msg = build_wecom_message(NotificationPayload(video, "> 伪引用", None))

    assert "&lt;font color=&quot;warning&quot;&gt;" in msg
    assert "\n\\# 伪标题" in msg
    assert "\\&gt; 伪引用" not in msg
    assert "&gt; 伪引用" in msg


def test_split_wecom_message_uses_utf8_byte_limit_without_losing_text():
    from ashare.notify.wecom import split_message as split_wecom_message

    text = "中文换行\n" + "好" * 10
    parts = split_wecom_message(text, limit=10)

    assert "".join(parts) == text
    assert len(parts) > 1
    assert all(len(part.encode("utf-8")) <= 10 for part in parts)


def test_wecom_notifier_posts_markdown_and_accepts_errcode_zero():
    from ashare.notify.base import NotificationPayload
    from ashare.notify.wecom import WeComNotifier

    posted, throttles = [], []

    def poster(url, payload):
        posted.append((url, payload))
        return {"errcode": 0, "errmsg": "ok"}

    notifier = WeComNotifier(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
        poster=poster,
        throttle=lambda: throttles.append(True),
    )

    result = notifier.send(NotificationPayload(_video(), "原文", _analysis()))

    assert isinstance(result, Ok)
    assert len(posted) == 1
    assert posted[0][1]["msgtype"] == "markdown"
    assert "整体看多半导体" in posted[0][1]["markdown"]["content"]
    assert throttles == [True]


def test_wecom_notifier_returns_business_error_without_leaking_key():
    from ashare.notify.base import NotificationPayload
    from ashare.notify.wecom import WeComNotifier

    calls = []

    def poster(url, payload):
        calls.append(payload)
        return {"errcode": 93000, "errmsg": "invalid webhook secret-key"}

    notifier = WeComNotifier(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret-key",
        poster=poster,
        throttle=lambda: None,
    )

    result = notifier.send(NotificationPayload(_video(), "原文", _analysis()))

    assert isinstance(result, Err)
    assert "93000" in result.error
    assert "invalid webhook" in result.error
    assert "secret-key" not in result.error
    assert len(calls) == 1


def test_wecom_notifier_retries_one_transient_failure():
    from ashare.notify.base import NotificationPayload
    from ashare.notify.wecom import WeComNotifier

    attempts, throttles = [], []

    def poster(url, payload):
        attempts.append(payload)
        if len(attempts) == 1:
            raise RuntimeError("temporary network error")
        return {"errcode": 0, "errmsg": "ok"}

    notifier = WeComNotifier(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
        poster=poster,
        throttle=lambda: throttles.append(True),
    )

    result = notifier.send(NotificationPayload(_video(), "原文", _analysis()))

    assert isinstance(result, Ok)
    assert len(attempts) == 2
    assert throttles == [True, True]


def test_wecom_notifier_returns_error_for_malformed_response():
    from ashare.notify.base import NotificationPayload
    from ashare.notify.wecom import WeComNotifier

    notifier = WeComNotifier(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
        poster=lambda url, payload: ["unexpected"],
        throttle=lambda: None,
    )

    result = notifier.send(NotificationPayload(_video(), "原文", _analysis()))

    assert isinstance(result, Err)
    assert result.error == "企业微信响应格式异常"


def test_wecom_http_poster_suppresses_webhook_url_logs(monkeypatch):
    from ashare.notify.wecom import _httpx_poster

    httpx_logger = logging.getLogger("httpx")
    old_level = httpx_logger.level
    httpx_logger.setLevel(logging.INFO)
    raised = []

    class Resp:
        def raise_for_status(self):
            raised.append(True)

        def json(self):
            return {"errcode": 0, "errmsg": "ok"}

    def fake_post(url, json, timeout):
        assert httpx_logger.getEffectiveLevel() >= logging.WARNING
        assert timeout == 10
        return Resp()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)

    assert _httpx_poster("https://example.invalid/?key=secret", {})["errcode"] == 0
    assert raised == [True]
    assert httpx_logger.level == logging.INFO
    httpx_logger.setLevel(old_level)


def test_wecom_throttle_enforces_minimum_interval_with_injected_time():
    from ashare.notify.wecom import _make_throttle

    now = {"value": 10.0}
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now["value"] += seconds

    throttle = _make_throttle(
        interval=3.1, clock=lambda: now["value"], sleep=sleep,
    )

    throttle()
    now["value"] += 1.0
    throttle()

    assert sleeps == [2.1]
