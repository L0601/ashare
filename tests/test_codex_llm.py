"""Codex CLI 分析后端：真实子进程边界、隔离参数与错误处理。"""

from __future__ import annotations

import json

from ashare.config import LLMConfig
from ashare.models import AnalysisType
from ashare.result import Err, Ok

GOOD = json.dumps({
    "title": "看多半导体",
    "summary": "国产替代趋势明确",
    "sectors": ["半导体"],
    "opinions": [],
}, ensure_ascii=False)


def _fake_codex(
    tmp_path, *, output: str = GOOD, delay: float = 0, exit_code: int = 0,
):
    executable = tmp_path / "codex"
    capture = tmp_path / "capture.json"
    executable.write_text(
        f"""#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time

args = sys.argv[1:]
stdin = sys.stdin.read()
time.sleep({delay!r})
output_path = pathlib.Path(args[args.index('--output-last-message') + 1])
schema_path = pathlib.Path(args[args.index('--output-schema') + 1])
pathlib.Path({str(capture)!r}).write_text(json.dumps({{
    'args': args,
    'stdin': stdin,
    'codex_home': os.environ.get('CODEX_HOME'),
    'schema': json.loads(schema_path.read_text()),
}}, ensure_ascii=False), encoding='utf-8')
output_path.write_text({output!r}, encoding='utf-8')
sys.exit({exit_code})
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, capture


def test_codex_backend_runs_isolated_and_keeps_transcript_out_of_arguments(tmp_path):
    from ashare.llm.codex import CodexCLIBackend, CodexRuntime

    executable, capture_path = _fake_codex(tmp_path)
    codex_home = tmp_path / "clean-codex"
    codex_home.mkdir()
    backend = CodexCLIBackend(CodexRuntime(executable, codex_home, timeout=2))

    result = backend.generate("系统规则", "私密口播文字")

    assert isinstance(result, Ok)
    assert result.value == GOOD
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    args = capture["args"]
    assert "--ephemeral" in args
    assert "--ignore-user-config" in args
    assert "--skip-git-repo-check" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert args[-1] == "-"
    assert "私密口播文字" not in " ".join(args)
    assert "私密口播文字" in capture["stdin"]
    assert capture["codex_home"] == str(codex_home)
    assert "opinions" in capture["schema"]["required"]


def test_codex_backend_uses_general_schema_for_general_user(tmp_path):
    from ashare.llm.codex import CodexCLIBackend, CodexRuntime

    executable, capture_path = _fake_codex(tmp_path)
    codex_home = tmp_path / "clean-codex"
    codex_home.mkdir()
    backend = CodexCLIBackend(
        CodexRuntime(executable, codex_home, timeout=2),
        analysis_type=AnalysisType.GENERAL,
    )

    result = backend.generate("通用规则", "口播文字")

    assert isinstance(result, Ok)
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    schema = capture["schema"]
    assert schema["required"] == ["title", "summary", "tags", "content"]
    assert "opinions" not in schema["properties"]
    assert "完成通用内容分析" in capture["stdin"]
    assert "金融观点抽取" not in capture["stdin"]


def test_codex_backend_times_out_without_leaking_transcript(tmp_path):
    from ashare.llm.codex import CodexCLIBackend, CodexRuntime

    executable, _ = _fake_codex(tmp_path, delay=1)
    codex_home = tmp_path / "clean-codex"
    codex_home.mkdir()
    backend = CodexCLIBackend(CodexRuntime(executable, codex_home, timeout=0.05))

    result = backend.generate("系统规则", "绝密口播内容")

    assert isinstance(result, Err)
    assert "超时" in result.error
    assert "绝密口播内容" not in result.error


def test_codex_backend_rejects_nonzero_exit_even_with_output(tmp_path):
    from ashare.llm.codex import CodexCLIBackend, CodexRuntime

    executable, _ = _fake_codex(tmp_path, exit_code=7)
    codex_home = tmp_path / "clean-codex"
    codex_home.mkdir()
    backend = CodexCLIBackend(CodexRuntime(executable, codex_home, timeout=2))

    result = backend.generate("系统规则", "口播文字")

    assert isinstance(result, Err)
    assert result.error == "Codex 执行失败（exit=7）"


def test_codex_backend_rejects_empty_final_message(tmp_path):
    from ashare.llm.codex import CodexCLIBackend, CodexRuntime

    executable, _ = _fake_codex(tmp_path, output="")
    codex_home = tmp_path / "clean-codex"
    codex_home.mkdir()
    backend = CodexCLIBackend(CodexRuntime(executable, codex_home, timeout=2))

    result = backend.generate("系统规则", "口播文字")

    assert isinstance(result, Err)
    assert result.error == "Codex 返回空结果"


def test_detect_codex_runtime_requires_home_and_executable(tmp_path):
    from ashare.llm.codex import detect_codex_runtime

    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    missing_home = tmp_path / "missing-home"

    assert detect_codex_runtime(
        120, home=missing_home, path_lookup=lambda _: str(executable),
    ) is None

    codex_home = tmp_path / "clean-codex"
    codex_home.mkdir()
    runtime = detect_codex_runtime(
        120, home=codex_home, path_lookup=lambda _: str(executable),
    )

    assert runtime is not None
    assert runtime.executable == executable
    assert runtime.home == codex_home
    assert runtime.timeout == 120


def test_extractor_factory_uses_detected_codex_as_primary(tmp_path):
    from ashare.llm.codex import CodexRuntime
    from ashare.llm.extractor import make_extractor

    executable, _ = _fake_codex(tmp_path)
    codex_home = tmp_path / "clean-codex"
    codex_home.mkdir()
    runtime = CodexRuntime(executable, codex_home, timeout=2)

    class FallbackMustNotRun:
        @property
        def chat(self):
            raise AssertionError("Codex 可用时不应调用现有 LLM")

    cfg = LLMConfig(base_url="http://fallback", api_key="k", model="fallback")
    extractor = make_extractor(
        cfg, client=FallbackMustNotRun(), detector=lambda timeout: runtime,
    )

    result = extractor.extract("v1", "标题", "口播文字")

    assert isinstance(result, Ok)
    assert result.value.llm_model == "codex-cli"


def test_extractor_factory_wires_general_type_and_prompt(tmp_path):
    from ashare.llm.codex import CodexRuntime
    from ashare.llm.extractor import make_extractor

    output = json.dumps({
        "title": "新品", "summary": "摘要", "tags": ["科技"],
        "content": "通用正文",
    }, ensure_ascii=False)
    executable, capture_path = _fake_codex(tmp_path, output=output)
    codex_home = tmp_path / "clean-codex"
    codex_home.mkdir()
    runtime = CodexRuntime(executable, codex_home, timeout=2)
    cfg = LLMConfig(base_url="http://fallback", api_key="k", model="fallback")

    extractor = make_extractor(
        cfg, detector=lambda timeout: runtime,
        analysis_type=AnalysisType.GENERAL, system_prompt="关注产品能力。",
    )
    result = extractor.extract("v1", "标题", "口播文字")

    assert isinstance(result, Ok)
    assert result.value.analysis_type == AnalysisType.GENERAL
    assert result.value.content == "通用正文"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    assert "content" in capture["schema"]["required"]
    assert "关注产品能力" in capture["stdin"]
