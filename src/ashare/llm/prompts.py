"""LLM 抽取用的提示词。

要求模型输出严格 JSON，便于结构化落库；同时给出整体摘要与逐条观点。
"""

from __future__ import annotations

SYSTEM_PROMPT = """你是一名专业的金融分析助理。用户会给你一段财经类短视频的口播文字（由语音识别得到，可能有少量错别字）。
你的任务：从中抽取博主对【具体行业/板块】或【具体个股】的观点。

只抽取明确表达的看法，不要臆测、不要编造代码。严格按下面的 JSON 格式输出，不要输出多余文字：

{
  "title": "给本视频起一个不超过20字的精炼标题，概括核心观点（这类视频通常没有文案标题）",
  "summary": "用 1-3 句话概括本视频的整体金融观点",
  "sectors": ["涉及的行业/板块标签", "..."],
  "opinions": [
    {
      "target_name": "标的名称（行业名或个股名）",
      "target_code": "个股代码，没有则留空字符串",
      "target_type": "stock 或 industry",
      "stance": "bullish(看多) / bearish(看空) / neutral(中性观望)",
      "confidence": 0.0到1.0之间的数字,
      "logic": "博主给出的核心逻辑/理由",
      "quote": "支撑该观点的一句原话引用"
    }
  ]
}

若视频中没有任何对行业或个股的明确观点，opinions 返回空数组。"""


def build_user_prompt(title: str, transcript: str) -> str:
    """拼装用户侧输入：标题 + 转写正文。"""
    return f"视频标题：{title}\n\n口播文字：\n{transcript}"
