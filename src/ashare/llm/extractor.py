"""Codex 优先、兼容 OpenAI 的 LLM 兜底，并做防御性解析。

解析逻辑是核心（可单测）：从 LLM 文本里抠出 JSON、做枚举归一化、容错。
网络调用是 IO，与解析分离；调用失败/解析失败都用 Result 表达。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from ..config import LLMConfig
from ..models import Analysis, AnalysisType, Opinion, Stance, TargetType
from ..result import Err, Ok, Result
from .prompts import build_user_prompt, default_system_prompt, output_contract

logger = logging.getLogger("ashare.llm")

# 枚举归一化：兼容中英文与常见同义表达
_STANCE_MAP = {
    "bullish": Stance.BULLISH, "多": Stance.BULLISH, "看多": Stance.BULLISH,
    "positive": Stance.BULLISH, "利好": Stance.BULLISH, "看好": Stance.BULLISH,
    "bearish": Stance.BEARISH, "空": Stance.BEARISH, "看空": Stance.BEARISH,
    "negative": Stance.BEARISH, "利空": Stance.BEARISH,
}
_TYPE_MAP = {
    "stock": TargetType.STOCK, "个股": TargetType.STOCK, "股票": TargetType.STOCK,
    "industry": TargetType.INDUSTRY, "行业": TargetType.INDUSTRY, "板块": TargetType.INDUSTRY,
}


@dataclass
class ParsedAnalysis:
    """解析后的结构化结果（不含 aweme_id/model 等元信息）。"""

    title: str = ""
    summary: str = ""
    sectors: list[str] = field(default_factory=list)
    opinions: list[Opinion] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    content: str = ""


class TextGenerationBackend(Protocol):
    """可插拔文本生成后端；抽取器负责统一解析和落业务模型。"""

    model_name: str

    def generate(self, system_prompt: str, user_prompt: str) -> Result[str, str]: ...


def _normalize_stance(value: str) -> Stance:
    return _STANCE_MAP.get(str(value).strip().lower(), Stance.NEUTRAL)


def _normalize_type(value: str, has_code: bool) -> TargetType:
    t = _TYPE_MAP.get(str(value).strip().lower())
    if t:
        return t
    return TargetType.STOCK if has_code else TargetType.INDUSTRY


def _extract_json(raw: str) -> str | None:
    """从 LLM 输出里抠出 JSON 对象：先去 ``` 代码围栏，再取首尾大括号。"""
    text = re.sub(r"```(?:json)?", "", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start:end + 1]


def parse_llm_output(
    raw: str, analysis_type: AnalysisType = AnalysisType.FINANCIAL,
) -> Result[ParsedAnalysis, str]:
    """把 LLM 原始文本解析为 ParsedAnalysis。纯函数，可单测。"""
    snippet = _extract_json(raw)
    if not snippet:
        return Err("未在 LLM 输出中找到 JSON")
    try:
        data = json.loads(snippet)
    except json.JSONDecodeError as e:
        return Err(f"JSON 解析失败：{e}")
    if not isinstance(data, dict):
        return Err("LLM 输出不是 JSON 对象")

    kind = AnalysisType(analysis_type)
    if kind == AnalysisType.GENERAL and not _valid_general_shape(data):
        return Err("通用分析输出缺少 title/summary/tags/content 或字段类型错误")
    opinions = []
    if kind == AnalysisType.FINANCIAL:
        opinions = [
            _to_opinion(item) for item in data.get("opinions", [])
            if isinstance(item, dict)
        ]
    return Ok(ParsedAnalysis(
        title=str(data.get("title", "")).strip(),
        summary=str(data.get("summary", "")).strip(),
        sectors=[str(s) for s in data.get("sectors", []) if s],
        opinions=opinions,
        tags=[str(tag) for tag in data.get("tags", []) if tag],
        content=str(data.get("content", "")).strip(),
    ))


def _valid_general_shape(data: dict) -> bool:
    return (
        isinstance(data.get("title"), str)
        and isinstance(data.get("summary"), str)
        and isinstance(data.get("tags"), list)
        and all(isinstance(tag, str) for tag in data["tags"])
        and isinstance(data.get("content"), str)
    )


def _to_opinion(o: dict) -> Opinion:
    code = str(o.get("target_code", "")).strip()
    return Opinion(
        target_name=str(o.get("target_name", "")).strip(),
        target_code=code,
        target_type=_normalize_type(o.get("target_type", ""), bool(code)),
        stance=_normalize_stance(o.get("stance", "")),
        confidence=_safe_float(o.get("confidence", 0)),
        logic=str(o.get("logic", "")).strip(),
        quote=str(o.get("quote", "")).strip(),
    )


def _safe_float(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


class LLMExtractor:
    """封装兼容 OpenAI 的 LLM 调用 + 解析，产出可落库的 Analysis。"""

    def __init__(
        self, cfg: LLMConfig, client=None,
        primary: TextGenerationBackend | None = None,
        analysis_type: AnalysisType = AnalysisType.FINANCIAL,
        system_prompt: str | None = None,
    ):
        self._cfg = cfg
        self._client = client  # 可注入假 client 测试
        self._primary = primary
        self._analysis_type = AnalysisType(analysis_type)
        configured_prompt = (
            system_prompt
            if system_prompt is not None
            else default_system_prompt(self._analysis_type)
        )
        self._system_prompt = _effective_prompt(
            configured_prompt, self._analysis_type,
        )

    def _ensure_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self._cfg.base_url, api_key=self._cfg.api_key)
        return self._client

    def extract(self, aweme_id: str, title: str, transcript: str) -> Result[Analysis, str]:
        """对单条转写抽取观点。

        契约：只要 LLM 调用成功就返回 Ok(Analysis)，且永远保留 AI 输入与原始输出（留痕）。
        解析失败不算整体失败——把原文留给人工复核，summary 标注解析失败、opinions 置空，
        这样流水线可正常完成、看板可见原始输出。仅 LLM 调用本身失败才返回 Err（可重试）。
        """
        user_prompt = build_user_prompt(title, transcript)
        primary, primary_error = self._try_primary(aweme_id, user_prompt)
        if primary is not None:
            return Ok(primary)
        if primary_error:
            logger.warning("Codex 不可用，回退现有 LLM：%s", primary_error)
        try:
            raw = self._call(user_prompt)
        except Exception as e:
            fallback_error = f"LLM 调用失败：{e}"
            if primary_error:
                return Err(f"Codex 失败：{primary_error}；{fallback_error}")
            return Err(fallback_error)

        parsed = parse_llm_output(raw, self._analysis_type)
        return Ok(_build_analysis(
            aweme_id, self._cfg.model, user_prompt, raw, parsed,
            self._analysis_type, self._system_prompt,
        ))

    def _try_primary(
        self, aweme_id: str, user_prompt: str,
    ) -> tuple[Analysis | None, str]:
        if self._primary is None:
            return None, ""
        try:
            generated = self._primary.generate(self._system_prompt, user_prompt)
        except Exception as error:
            return None, f"{type(error).__name__}: {error}"
        if not generated.is_ok():
            return None, str(generated.error)
        parsed = parse_llm_output(generated.value, self._analysis_type)
        if not parsed.is_ok():
            return None, str(parsed.error)
        return _build_analysis(
            aweme_id, self._primary.model_name, user_prompt,
            generated.value, parsed, self._analysis_type, self._system_prompt,
        ), ""

    def _call(self, user_prompt: str) -> str:
        resp = self._ensure_client().chat.completions.create(
            model=self._cfg.model,
            temperature=self._cfg.temperature,
            timeout=self._cfg.timeout,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content or ""


def _build_analysis(
    aweme_id: str,
    model_name: str,
    user_prompt: str,
    raw: str,
    parsed: Result[ParsedAnalysis, str],
    analysis_type: AnalysisType,
    system_prompt: str,
) -> Analysis:
    analysis = Analysis(
        aweme_id=aweme_id, llm_model=model_name,
        prompt_input=user_prompt, raw_output=raw, analysis_type=analysis_type,
        system_prompt=system_prompt,
    )
    if not parsed.is_ok():
        analysis.summary = f"(观点解析失败：{parsed.error}，请查看原始输出)"
        return analysis
    analysis.title = parsed.value.title
    analysis.summary = parsed.value.summary
    analysis.sectors = parsed.value.sectors
    analysis.opinions = parsed.value.opinions
    analysis.tags = parsed.value.tags
    analysis.content = parsed.value.content
    for opinion in analysis.opinions:
        opinion.aweme_id = aweme_id
    return analysis


def _effective_prompt(system_prompt: str, analysis_type: AnalysisType) -> str:
    custom = system_prompt.strip()
    contract = output_contract(analysis_type)
    return f"{custom}\n\n{contract}" if custom else contract


def make_extractor(
    cfg: LLMConfig, *, client=None, detector=None,
    analysis_type: AnalysisType = AnalysisType.FINANCIAL,
    system_prompt: str | None = None,
) -> LLMExtractor:
    """自动探测本机 Codex，并以现有 OpenAI-compatible LLM 作为兜底。"""
    from .codex import CodexCLIBackend, detect_codex_runtime

    detect = detector or detect_codex_runtime
    kind = AnalysisType(analysis_type)
    prompt = system_prompt if system_prompt is not None else default_system_prompt(kind)
    runtime = detect(cfg.timeout)
    primary = CodexCLIBackend(runtime, analysis_type=kind) if runtime else None
    return LLMExtractor(
        cfg, client=client, primary=primary,
        analysis_type=kind, system_prompt=prompt,
    )
