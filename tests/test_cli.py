"""CLI 诊断命令应复用生产装配策略。"""

from types import SimpleNamespace

from ashare.result import Ok


def test_check_llm_uses_preferred_extractor_factory(monkeypatch, capsys):
    from ashare import cli
    from ashare.llm import extractor

    cfg = SimpleNamespace(llm=SimpleNamespace())
    analysis = SimpleNamespace(
        llm_model="codex-cli", summary="看好半导体", sectors=["半导体"],
        opinions=[],
    )
    seen = []

    class Preferred:
        def extract(self, aweme_id, title, transcript):
            return Ok(analysis)

    def factory(llm_cfg):
        seen.append(llm_cfg)
        return Preferred()

    class LegacyMustNotRun:
        def __init__(self, *args, **kwargs):
            raise AssertionError("诊断命令不应绕过 Codex 优先工厂")

    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(extractor, "make_extractor", factory)
    monkeypatch.setattr(extractor, "LLMExtractor", LegacyMustNotRun)

    cli.check_llm()

    assert seen == [cfg.llm]
    assert "model=codex-cli" in capsys.readouterr().out
