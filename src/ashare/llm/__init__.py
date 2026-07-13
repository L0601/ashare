"""Codex 优先、兼容 OpenAI 的 LLM 兜底的金融观点抽取层。"""

from .extractor import (
    LLMExtractor,
    ParsedAnalysis,
    TextGenerationBackend,
    make_extractor,
    parse_llm_output,
)

__all__ = [
    "LLMExtractor", "ParsedAnalysis", "TextGenerationBackend",
    "make_extractor", "parse_llm_output",
]
