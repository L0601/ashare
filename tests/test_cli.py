"""CLI 诊断命令应复用生产装配策略。"""

from types import SimpleNamespace

from ashare.result import Ok


def test_run_once_processes_all_enabled_users(monkeypatch, capsys):
    from ashare import cli
    from ashare.pipeline import multi_user

    cfg = SimpleNamespace()
    calls = []
    monkeypatch.setattr(cli, "_effective_config", lambda: cfg)
    monkeypatch.setattr(
        multi_user, "run_all_users",
        lambda effective, *, limit: calls.append((effective, limit)) or {"processed": 3},
    )

    cli.run_once(limit=4)

    assert calls == [(cfg, 4)]
    assert "processed" in capsys.readouterr().out


def test_check_llm_uses_preferred_extractor_factory(monkeypatch, capsys):
    from ashare import cli
    from ashare.llm import extractor
    from ashare.models import AnalysisType

    cfg = SimpleNamespace(llm=SimpleNamespace())
    user = SimpleNamespace(
        analysis_type=AnalysisType.FINANCIAL, system_prompt="用户金融提示词",
    )
    analysis = SimpleNamespace(
        llm_model="codex-cli", summary="看好半导体", sectors=["半导体"],
        opinions=[],
    )
    seen = []

    class Preferred:
        def extract(self, aweme_id, title, transcript):
            return Ok(analysis)

    def factory(llm_cfg, *, analysis_type, system_prompt):
        seen.append((llm_cfg, analysis_type, system_prompt))
        return Preferred()

    class LegacyMustNotRun:
        def __init__(self, *args, **kwargs):
            raise AssertionError("诊断命令不应绕过 Codex 优先工厂")

    monkeypatch.setattr(cli, "_effective_config", lambda: cfg)
    monkeypatch.setattr(cli, "_tracked_user", lambda effective, user_id: user)
    monkeypatch.setattr(extractor, "make_extractor", factory)
    monkeypatch.setattr(extractor, "LLMExtractor", LegacyMustNotRun)

    cli.check_llm(user_id=7)

    assert seen == [(cfg.llm, AnalysisType.FINANCIAL, "用户金融提示词")]
    assert "model=codex-cli" in capsys.readouterr().out
