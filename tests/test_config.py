"""config 加载单测：正常加载、缺文件报错、默认值。"""

import pytest

from ashare.config import apply_overrides, editable_values, load_config

SAMPLE = """
douyin:
  cookie: "ck"
  author_sec_uid: "sec123"
  author_name: "创业魔法师"
llm:
  base_url: "https://api.deepseek.com/v1"
  api_key: "key"
  model: "deepseek-chat"
asr:
  engine: "fake"
"""


def test_load_config_ok(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.douyin.author_sec_uid == "sec123"
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.asr.engine == "fake"
    # 未提供的项走默认值
    assert cfg.douyin.latest_count == 10
    assert cfg.schedule.poll_interval_minutes == 90
    assert cfg.wecom.enabled is False
    assert cfg.wecom.webhook_url == ""


def test_load_config_paths(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.db_path.name == "ashare.db"
    assert cfg.videos_dir.name == "videos"


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def _base_cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    return load_config(p)


def test_apply_overrides_maps_db_values(tmp_path):
    cfg = _base_cfg(tmp_path)
    eff = apply_overrides(cfg, {
        "cookie": "new_ck", "author_sec_uid": "MS4w_new",
        "latest_count": "20", "poll_interval_minutes": "120",
        "telegram_enabled": "1", "telegram_bot_token": "tok", "telegram_chat_id": "-100",
        "wecom_enabled": "1",
    })
    assert eff.douyin.cookie == "new_ck"
    assert eff.douyin.author_sec_uid == "MS4w_new"
    assert eff.douyin.latest_count == 20          # 字符串转 int
    assert eff.schedule.poll_interval_minutes == 120
    assert eff.telegram.enabled is True           # "1" → bool True
    assert eff.telegram.bot_token == "tok"
    assert eff.telegram.chat_id == "-100"
    assert eff.wecom.enabled is True


def test_apply_overrides_bool_false(tmp_path):
    cfg = _base_cfg(tmp_path)
    assert apply_overrides(cfg, {"telegram_enabled": "0"}).telegram.enabled is False
    assert apply_overrides(cfg, {"telegram_enabled": "true"}).telegram.enabled is True
    # 原 cfg 不被改动（apply 走拷贝）
    assert cfg.douyin.cookie == "ck"


def test_apply_overrides_skips_empty_and_invalid(tmp_path):
    cfg = _base_cfg(tmp_path)
    eff = apply_overrides(cfg, {"cookie": "", "latest_count": "abc"})
    assert eff.douyin.cookie == "ck"              # 空值不覆盖
    assert eff.douyin.latest_count == 10          # 非法 int 不覆盖（保留默认）


def test_editable_values_reads_effective(tmp_path):
    cfg = _base_cfg(tmp_path)
    vals = editable_values(cfg)
    assert vals["author_sec_uid"] == "sec123"
    assert vals["latest_count"] == 10
    assert set(vals) == {"cookie", "author_sec_uid", "latest_count", "poll_interval_minutes",
                         "telegram_enabled", "telegram_bot_token", "telegram_chat_id",
                         "wecom_enabled"}
