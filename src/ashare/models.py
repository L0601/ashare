"""领域数据模型与枚举。

把"业务概念"集中在此，db 层负责持久化、pipeline 层负责流转，
模型本身不含 IO，便于在任何地方构造与单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Stage(str, Enum):
    """流水线的五个阶段，顺序即依赖顺序。"""

    DISCOVER = "discover"        # 发现：抓取博主最新视频元数据
    DOWNLOAD = "download"        # 下载：无水印 mp4
    EXTRACT_AUDIO = "extract_audio"  # 抽音轨：ffmpeg -> 16k 单声道 wav
    TRANSCRIBE = "transcribe"    # 转写：本地 ASR 语音转文字
    EXTRACT = "extract"          # 分析：LLM 生成结构化内容
    NOTIFY = "notify"            # 推送：把结果发到全部启用渠道

    @classmethod
    def ordered(cls) -> list["Stage"]:
        """按执行顺序返回各阶段（discover 一般在抓取期单独完成）。"""
        return [cls.DOWNLOAD, cls.EXTRACT_AUDIO, cls.TRANSCRIBE, cls.EXTRACT, cls.NOTIFY]


class Status(str, Enum):
    """阶段 / 视频的处理状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class Stance(str, Enum):
    """对某标的的多空倾向。"""

    BULLISH = "bullish"   # 看多
    BEARISH = "bearish"   # 看空
    NEUTRAL = "neutral"   # 中性/观望


class TargetType(str, Enum):
    INDUSTRY = "industry"  # 行业/板块
    STOCK = "stock"        # 个股


class AnalysisType(str, Enum):
    """用户选择的内容分析结构。"""

    FINANCIAL = "financial"
    GENERAL = "general"


@dataclass(frozen=True)
class TrackedUser:
    """一个受监控的抖音用户及其分析策略。"""

    sec_uid: str
    name: str
    analysis_type: AnalysisType
    system_prompt: str
    enabled: bool = True
    id: Optional[int] = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class Video:
    """一个抖音视频的元数据。"""

    aweme_id: str
    author_sec_uid: str
    title: str
    video_url: str               # 网页可访问的视频主页链接
    publish_time: int            # 发布时间，Unix 秒
    author_name: str = ""        # 博主昵称（展示与推送用）
    cover_url: str = ""
    duration_ms: int = 0
    download_url: str = ""        # 无水印下载地址（抓取时临时获取，不入库主表）
    overall_status: Status = Status.PENDING
    discovered_at: int = 0
    updated_at: int = 0


@dataclass
class Opinion:
    """从视频中抽取的一条结构化金融观点。"""

    target_name: str                       # 标的名称（行业名或个股名）
    target_type: TargetType
    stance: Stance
    logic: str                             # 核心逻辑/理由
    target_code: str = ""                  # 个股代码（如有）
    confidence: float = 0.0                # 0~1 信心
    quote: str = ""                        # 原话引用
    aweme_id: str = ""
    id: Optional[int] = None


@dataclass
class Analysis:
    """一次 LLM 抽取的整体结果（逐条观点单独存 Opinion）。"""

    aweme_id: str
    llm_model: str
    prompt_input: str                      # 喂给 LLM 的完整输入（留痕）
    raw_output: str                        # LLM 原始输出（留痕，便于排错）
    analysis_type: AnalysisType = AnalysisType.FINANCIAL
    system_prompt: str = ""                # 实际使用的系统提示词快照
    tags: list[str] = field(default_factory=list)  # 通用分析标签
    content: str = ""                      # 通用分析正文
    title: str = ""                        # LLM 生成的精炼标题（视频无原生文案时用，不入库，仅回填 videos.title）
    summary: str = ""                      # 整体观点摘要
    sectors: list[str] = field(default_factory=list)  # 涉及板块标签
    opinions: list[Opinion] = field(default_factory=list)
    created_at: int = 0


@dataclass
class Transcript:
    """一次 ASR 转写结果。"""

    aweme_id: str
    text: str
    asr_engine: str
    audio_seconds: float = 0.0
    created_at: int = 0
