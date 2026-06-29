"""配置加载：集中所有敏感/可变项，从 config.yaml 读取并用 pydantic 校验。

设计：配置通过参数传入各模块（依赖注入），核心逻辑不直接读环境/全局单例，
这样测试时可以构造任意配置传进去。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class DouyinConfig(BaseModel):
    cookie: str = ""
    author_sec_uid: str = ""
    author_name: str = ""
    latest_count: int = 10
    # 桌面浏览器 UA：与 PC 网页 cookie、web 接口、F2 的桌面指纹一致，降低风控误判
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
    )


class LLMConfig(BaseModel):
    base_url: str
    api_key: str
    model: str
    timeout: int = 120
    temperature: float = 0.2


class ASRConfig(BaseModel):
    engine: str = "mlx"          # mlx | transformers | fake
    model: str = "Qwen/Qwen3-ASR-1.7B"
    language: str = "Chinese"


class ScheduleConfig(BaseModel):
    poll_interval_minutes: int = 90
    request_min_seconds: float = 5
    request_jitter_seconds: float = 3


class StorageConfig(BaseModel):
    data_dir: str = "data"


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class AppConfig(BaseModel):
    douyin: DouyinConfig
    llm: LLMConfig
    asr: ASRConfig = Field(default_factory=ASRConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    web: WebConfig = Field(default_factory=WebConfig)

    @property
    def db_path(self) -> Path:
        return Path(self.storage.data_dir) / "ashare.db"

    @property
    def videos_dir(self) -> Path:
        return Path(self.storage.data_dir) / "videos"


# 可在 Web 后台运行时修改、并存入 DB 覆盖文件默认值的配置项。
# 原则：常用/操作性的放这里；UA、LLM 密钥、模型路径等固化项留在文件，不进后台。
EDITABLE_SETTINGS = [
    {"key": "cookie", "section": "douyin", "field": "cookie", "type": "str",
     "label": "抖音 Cookie", "secret": True},
    {"key": "author_sec_uid", "section": "douyin", "field": "author_sec_uid", "type": "str",
     "label": "博主 sec_uid"},
    {"key": "latest_count", "section": "douyin", "field": "latest_count", "type": "int",
     "label": "最近 N 条（抓取并处理）"},
    {"key": "poll_interval_minutes", "section": "schedule", "field": "poll_interval_minutes",
     "type": "int", "label": "轮询间隔(分钟)"},
]


def apply_overrides(cfg: AppConfig, overrides: dict) -> AppConfig:
    """把 DB 里的运行时配置覆盖到（拷贝的）AppConfig 上。空值/非法值跳过，保留文件默认。"""
    cfg = cfg.model_copy(deep=True)
    for spec in EDITABLE_SETTINGS:
        raw = overrides.get(spec["key"])
        if raw is None or str(raw) == "":
            continue
        try:
            value = int(raw) if spec["type"] == "int" else str(raw)
        except (TypeError, ValueError):
            continue  # 非法值不覆盖
        setattr(getattr(cfg, spec["section"]), spec["field"], value)
    return cfg


def editable_values(cfg: AppConfig) -> dict:
    """读取当前生效值（供后台表单回填）。"""
    out = {}
    for spec in EDITABLE_SETTINGS:
        out[spec["key"]] = getattr(getattr(cfg, spec["section"]), spec["field"])
    return out


def _default_config_path() -> Path:
    """优先用环境变量 ASHARE_CONFIG 指定的路径，否则项目根的 config.yaml。"""
    env = os.environ.get("ASHARE_CONFIG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "config.yaml"


def load_config(path: str | Path | None = None) -> AppConfig:
    """从 yaml 文件加载并校验配置。path 缺省时走默认查找逻辑。"""
    cfg_path = Path(path) if path else _default_config_path()
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"配置文件不存在：{cfg_path}。请复制 config.example.yaml 为 config.yaml 并填写。"
        )
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return AppConfig(**raw)
