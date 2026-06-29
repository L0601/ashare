"""调用兼容 OpenAI 的 LLM 抽取金融观点 + 防御性解析。

解析逻辑是核心（可单测）：从 LLM 文本里抠出 JSON、做枚举归一化、容错。
网络调用是 IO，与解析分离；调用失败/解析失败都用 Result 表达。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..config import LLMConfig
from ..models import Analysis, Opinion, Stance, TargetType
from ..result import Err, Ok, Result
from .prompts import SYSTEM_PROMPT, build_user_prompt

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


def parse_llm_output(raw: str) -> Result[ParsedAnalysis, str]:
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

    opinions = [_to_opinion(o) for o in data.get("opinions", []) if isinstance(o, dict)]
    return Ok(ParsedAnalysis(
        title=str(data.get("title", "")).strip(),
        summary=str(data.get("summary", "")).strip(),
        sectors=[str(s) for s in data.get("sectors", []) if s],
        opinions=opinions,
    ))


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

    def __init__(self, cfg: LLMConfig, client=None):
        self._cfg = cfg
        self._client = client  # 可注入假 client 测试

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
        try:
            raw = self._call(user_prompt)
        except Exception as e:
            return Err(f"LLM 调用失败：{e}")

        analysis = Analysis(
            aweme_id=aweme_id, llm_model=self._cfg.model,
            prompt_input=user_prompt, raw_output=raw,
        )
        parsed = parse_llm_output(raw)
        if parsed.is_ok():
            analysis.title = parsed.value.title
            analysis.summary = parsed.value.summary
            analysis.sectors = parsed.value.sectors
            analysis.opinions = parsed.value.opinions
            for op in analysis.opinions:
                op.aweme_id = aweme_id
        else:
            analysis.summary = f"(观点解析失败：{parsed.error}，请查看原始输出)"
        return Ok(analysis)

    def _call(self, user_prompt: str) -> str:
        resp = self._ensure_client().chat.completions.create(
            model=self._cfg.model,
            temperature=self._cfg.temperature,
            timeout=self._cfg.timeout,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content or ""
