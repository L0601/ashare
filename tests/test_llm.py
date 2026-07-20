"""LLM 解析与抽取单测：JSON 抠取、枚举归一化、容错、假 client 端到端。"""

import json

from types import SimpleNamespace

from ashare.config import LLMConfig
from ashare.llm.extractor import LLMExtractor, parse_llm_output
from ashare.models import AnalysisType, Stance, TargetType
from ashare.result import Err, Ok

GOOD = """```json
{
  "title": "看多新能源谨慎光伏",
  "summary": "整体看多新能源，谨慎看待光伏",
  "sectors": ["新能源", "光伏"],
  "opinions": [
    {"target_name": "宁德时代", "target_code": "300750", "target_type": "stock",
     "stance": "看多", "confidence": 0.9, "logic": "电池龙头", "quote": "我看好宁王"},
    {"target_name": "光伏", "target_code": "", "target_type": "行业",
     "stance": "bearish", "confidence": 1.5, "logic": "产能过剩", "quote": ""}
  ]
}
```"""

GENERAL = json.dumps({
    "title": "新品发布", "summary": "介绍了一款新产品",
    "tags": ["科技", "新品"], "content": "产品能力与适用人群。",
}, ensure_ascii=False)


def test_parse_good_output():
    r = parse_llm_output(GOOD)
    assert isinstance(r, Ok)
    p = r.value
    assert p.title == "看多新能源谨慎光伏"
    assert p.summary.startswith("整体看多")
    assert p.sectors == ["新能源", "光伏"]
    assert len(p.opinions) == 2
    o1, o2 = p.opinions
    assert o1.target_type == TargetType.STOCK
    assert o1.stance == Stance.BULLISH        # "看多" 归一化
    assert o1.confidence == 0.9
    assert o2.stance == Stance.BEARISH
    assert o2.target_type == TargetType.INDUSTRY  # "行业" 归一化
    assert o2.confidence == 1.0                # 1.5 被裁剪到 [0,1]


def test_parse_general_output():
    raw = json.dumps({
        "title": "新品发布", "summary": "介绍了一款新产品",
        "tags": ["科技", "新品"],
        "content": "内容重点包括产品能力、发布时间和适用人群。",
    }, ensure_ascii=False)

    result = parse_llm_output(raw, AnalysisType.GENERAL)

    assert isinstance(result, Ok)
    assert result.value.tags == ["科技", "新品"]
    assert result.value.content == "内容重点包括产品能力、发布时间和适用人群。"


def test_general_parser_rejects_financial_output_shape():
    result = parse_llm_output(GOOD, AnalysisType.GENERAL)

    assert isinstance(result, Err)
    assert "通用分析" in result.error


def test_default_prompts_are_available_for_both_analysis_types():
    from ashare.llm.prompts import default_system_prompt

    financial = default_system_prompt(AnalysisType.FINANCIAL)
    general = default_system_prompt(AnalysisType.GENERAL)

    assert "行业/板块" in financial
    assert "专业的内容观点提炼编辑" in general
    assert "非金融" not in general
    assert "主要观点" in general
    assert "分论点/理由" in general
    assert "文中依据" in general
    assert "转述误写成作者立场" in general
    assert financial != general


def test_parse_plain_json_no_fence():
    r = parse_llm_output('前言{"summary":"x","opinions":[]}后记')
    assert isinstance(r, Ok)
    assert r.value.summary == "x"
    assert r.value.opinions == []


def test_parse_no_json():
    assert isinstance(parse_llm_output("我没有输出 json"), Err)


def test_parse_invalid_json():
    assert isinstance(parse_llm_output("{坏的: json,,}"), Err)


def test_parse_unknown_stance_defaults_neutral():
    r = parse_llm_output('{"opinions":[{"target_name":"A","stance":"不确定"}]}')
    assert r.value.opinions[0].stance == Stance.NEUTRAL


class _FakeClient:
    """模拟 openai 客户端，返回预设文本。"""

    def __init__(self, content):
        msg = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=msg)
        resp = SimpleNamespace(choices=[choice])
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: resp)
        )


def _cfg():
    return LLMConfig(base_url="http://x", api_key="k", model="m")


def test_extractor_success():
    ext = LLMExtractor(_cfg(), client=_FakeClient(GOOD))
    r = ext.extract("v1", "标题", "口播文字")
    assert isinstance(r, Ok)
    a = r.value
    assert a.aweme_id == "v1"
    assert a.raw_output == GOOD            # 留痕原始输出
    assert "口播文字" in a.prompt_input    # 留痕 AI 输入
    assert len(a.opinions) == 2
    assert all(op.aweme_id == "v1" for op in a.opinions)


def test_general_extractor_uses_user_prompt_and_keeps_effective_snapshot():
    seen = {}

    class Primary:
        model_name = "codex-cli"

        def generate(self, system_prompt, user_prompt):
            seen["system_prompt"] = system_prompt
            return Ok(GENERAL)

    extractor = LLMExtractor(
        _cfg(), primary=Primary(), analysis_type=AnalysisType.GENERAL,
        system_prompt="重点分析产品能力，不要讨论投资价值。",
    )

    result = extractor.extract("v1", "标题", "口播文字")

    assert isinstance(result, Ok)
    assert "重点分析产品能力" in seen["system_prompt"]
    assert '"content"' in seen["system_prompt"]
    assert result.value.analysis_type == AnalysisType.GENERAL
    assert result.value.system_prompt == seen["system_prompt"]
    assert result.value.tags == ["科技", "新品"]
    assert result.value.content == "产品能力与适用人群。"


def test_general_fallback_uses_same_effective_system_prompt():
    seen = {}

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            seen["messages"] = kwargs["messages"]
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=GENERAL),
            )])

    extractor = LLMExtractor(
        _cfg(), client=Client(), analysis_type=AnalysisType.GENERAL,
        system_prompt="重点提取产品信息。",
    )

    result = extractor.extract("v1", "标题", "口播文字")

    assert isinstance(result, Ok)
    sent_prompt = seen["messages"][0]["content"]
    assert sent_prompt == result.value.system_prompt
    assert "重点提取产品信息" in sent_prompt
    assert '"tags"' in sent_prompt


def test_extractor_parse_failure_keeps_raw():
    """解析失败：仍返回 Ok 且保留原始输出，summary 标注失败、观点为空。"""
    ext = LLMExtractor(_cfg(), client=_FakeClient("模型抽风没给json"))
    r = ext.extract("v1", "标题", "口播")
    assert isinstance(r, Ok)
    assert r.value.raw_output == "模型抽风没给json"
    assert "解析失败" in r.value.summary
    assert r.value.opinions == []


def test_extractor_llm_call_failure_returns_err():
    """LLM 调用本身异常才算失败（可重试）。"""
    class Boom:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(
                create=self._raise))

        def _raise(self, **kw):
            raise RuntimeError("网络超时")

    r = LLMExtractor(_cfg(), client=Boom()).extract("v1", "t", "x")
    assert isinstance(r, Err)
    assert "LLM 调用失败" in r.error


def test_extractor_prefers_valid_primary_backend():
    class Primary:
        model_name = "codex-cli"

        def generate(self, system_prompt, user_prompt):
            return Ok(GOOD)

    class FallbackMustNotRun:
        @property
        def chat(self):
            raise AssertionError("Codex 成功时不应调用现有 LLM")

    result = LLMExtractor(
        _cfg(), client=FallbackMustNotRun(), primary=Primary(),
    ).extract("v1", "标题", "口播文字")

    assert isinstance(result, Ok)
    assert result.value.llm_model == "codex-cli"
    assert result.value.raw_output == GOOD
    assert len(result.value.opinions) == 2


def test_extractor_falls_back_when_primary_backend_fails():
    class FailedPrimary:
        model_name = "codex-cli"

        def generate(self, system_prompt, user_prompt):
            raise RuntimeError("Codex 执行超时")

    result = LLMExtractor(
        _cfg(), client=_FakeClient(GOOD), primary=FailedPrimary(),
    ).extract("v1", "标题", "口播文字")

    assert isinstance(result, Ok)
    assert result.value.llm_model == "m"
    assert result.value.raw_output == GOOD


def test_extractor_falls_back_when_primary_output_is_invalid():
    class InvalidPrimary:
        model_name = "codex-cli"

        def generate(self, system_prompt, user_prompt):
            return Ok("不是合法 JSON")

    result = LLMExtractor(
        _cfg(), client=_FakeClient(GOOD), primary=InvalidPrimary(),
    ).extract("v1", "标题", "口播文字")

    assert isinstance(result, Ok)
    assert result.value.llm_model == "m"
    assert result.value.raw_output == GOOD


def test_extractor_reports_primary_and_fallback_failures():
    class InvalidPrimary:
        model_name = "codex-cli"

        def generate(self, system_prompt, user_prompt):
            return Ok("Codex 没有返回 JSON")

    class FailedFallback:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(
                create=self._raise,
            ))

        def _raise(self, **kwargs):
            raise RuntimeError("现有 LLM 网络超时")

    result = LLMExtractor(
        _cfg(), client=FailedFallback(), primary=InvalidPrimary(),
    ).extract("v1", "标题", "口播文字")

    assert isinstance(result, Err)
    assert "未在 LLM 输出中找到 JSON" in result.error
    assert "现有 LLM 网络超时" in result.error
