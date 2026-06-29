"""LLM 解析与抽取单测：JSON 抠取、枚举归一化、容错、假 client 端到端。"""

from types import SimpleNamespace

from ashare.config import LLMConfig
from ashare.llm.extractor import LLMExtractor, parse_llm_output
from ashare.models import Stance, TargetType
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
