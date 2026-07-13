"""命令行入口：resolve-author / run-once / serve / schedule。"""

from __future__ import annotations

import logging

import typer

from .config import load_config

app = typer.Typer(add_completion=False, help="抖音财经博主金融观点抽取流水线")


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


def _effective_config():
    """文件配置 + DB 运行时覆盖（与 Web 后台改的设置保持一致）。"""
    from .config import apply_overrides
    from .db import Database

    cfg = load_config()
    db = Database(cfg.db_path)
    try:
        return apply_overrides(cfg, db.get_settings())
    finally:
        db.close()


@app.command("resolve-author")
def resolve_author(name_or_url: str):
    """把博主昵称或主页/分享链接解析为 sec_uid，回填到 config.yaml 的 douyin.author_sec_uid。"""
    from .douyin.client import F2DouyinClient

    cfg = _effective_config()
    res = F2DouyinClient(cfg).resolve_sec_uid(name_or_url)
    if res.is_ok():
        typer.echo(f"sec_uid = {res.value}")
        typer.echo("请将其填入 config.yaml 的 douyin.author_sec_uid。")
    else:
        typer.echo(f"解析失败：{res.error}", err=True)
        raise typer.Exit(code=1)


@app.command("reset-db")
def reset_db(
    files: bool = typer.Option(False, "--files", help="同时删除 data/videos 下的视频/音频等中间文件"),
):
    """重建数据库表并清空所有数据（可选一并清除中间文件）。"""
    from .db import Database

    cfg = load_config()
    db = Database(cfg.db_path)
    db.reset()
    db.close()
    typer.echo("✓ 数据库已重建，所有数据已清空")
    if files and cfg.videos_dir.exists():
        import shutil

        shutil.rmtree(cfg.videos_dir)
        typer.echo(f"✓ 已清空中间文件目录：{cfg.videos_dir}")


@app.command("run-once")
def run_once(limit: int = typer.Option(None, help="本轮最多处理几条（取最新，留空=全部）")):
    """立即跑一轮：发现新视频并处理未完成视频。"""
    _setup_logging()
    from .pipeline.runner import build_pipeline

    cfg = _effective_config()
    stats = build_pipeline(cfg).run_once(limit=limit)
    typer.echo(f"完成：{stats}")


@app.command("check-llm")
def check_llm():
    """[诊断] 用内置样例验证 Codex 优先与 LLM 回退；不联网抖音、不跑 ASR。"""
    from .llm.extractor import make_extractor

    sample = ("大家好，我今天重点看好半导体板块，国产替代是确定性趋势，中芯国际我特别看好。"
              "另外新能源短期我偏谨慎，产能有点过剩，光伏价格战还在打。")
    cfg = load_config()
    res = make_extractor(cfg.llm).extract("diag", "诊断样例", sample)
    if not res.is_ok():
        typer.echo(f"观点分析失败：{res.error}", err=True)
        raise typer.Exit(code=1)
    a = res.value
    typer.echo(f"✓ LLM 连通（model={a.llm_model}）")
    typer.echo(f"摘要：{a.summary}")
    typer.echo(f"板块：{a.sectors}")
    for o in a.opinions:
        typer.echo(f"  - [{o.stance.value}] {o.target_name}({o.target_code or '—'}) "
                   f"信心{o.confidence} | {o.logic}")


@app.command("list-videos")
def list_videos(count: int = typer.Option(5, help="拉取条数")):
    """[诊断] 调用抖音抓取，打印博主最新视频列表（只读，不下载）。验证 cookie/签名/sec_uid。"""
    from .douyin.client import F2DouyinClient

    cfg = _effective_config()
    sec = cfg.douyin.author_sec_uid
    if not sec:
        typer.echo("config.yaml 的 douyin.author_sec_uid 为空，请先 resolve-author。", err=True)
        raise typer.Exit(code=1)
    # 防呆：sec_uid 必须以 MS4w 开头。否则 F2 会静默返回 cookie 账号自己的视频，抓错账号。
    if not sec.startswith("MS4w"):
        typer.echo(f"author_sec_uid 不像合法 sec_uid（应以 MS4w 开头），当前值={sec!r}。\n"
                   f"请用 `ashare resolve-author \"<博主主页链接>\"` 解析后回填。", err=True)
        raise typer.Exit(code=1)
    res = F2DouyinClient(cfg).list_latest(sec, count)
    if not res.is_ok():
        typer.echo(f"抓取失败：{res.error}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"✓ 取到 {len(res.value)} 条：")
    for v in res.value:
        from .web.service import fmt_time
        typer.echo(f"  [{fmt_time(v.publish_time)}] {v.aweme_id} | {v.title}")


@app.command("download-one")
def download_one(
    video: str = typer.Argument(..., help="视频链接(含 /video/<id>)或直接给 aweme_id"),
    audio: bool = typer.Option(False, "--audio", help="下载后顺便用 ffmpeg 抽音轨"),
):
    """[诊断] 解析单个视频的无水印地址并下载 mp4（可选抽音轨）。只测抓取+下载，不跑 ASR/LLM。"""
    _setup_logging()
    from .douyin.client import F2DouyinClient, extract_aweme_id
    from .douyin.downloader import download_video
    from .media.audio import extract_audio
    from .storage import video_dir

    cfg = _effective_config()
    aweme_id = extract_aweme_id(video)
    typer.echo(f"aweme_id = {aweme_id}")

    client = F2DouyinClient(cfg)
    url_res = client.get_download_url(aweme_id)
    if not url_res.is_ok():
        typer.echo(f"解析下载地址失败：{url_res.error}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"✓ 解析到下载地址：{url_res.value[:90]}...")

    detail = client.list_latest(cfg.douyin.author_sec_uid, cfg.douyin.latest_count)
    video_meta = (
        next((v for v in detail.value if v.aweme_id == aweme_id), None)
        if detail.is_ok() else None
    )
    out_dir = video_dir(cfg.videos_dir, video_meta, aweme_id)
    mp4 = out_dir / "video.mp4"
    dl = download_video(url_res.value, mp4, user_agent=cfg.douyin.user_agent, cookie=cfg.douyin.cookie)
    if not dl.is_ok():
        typer.echo(f"下载失败：{dl.error}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"✓ 已下载：{mp4}（{mp4.stat().st_size // 1024} KB）")

    if audio:
        wav = out_dir / "audio.wav"
        au = extract_audio(mp4, wav)
        if not au.is_ok():
            typer.echo(f"抽音轨失败：{au.error}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"✓ 已抽音轨：{wav}（{wav.stat().st_size // 1024} KB）")


@app.command("check-asr")
def check_asr(wav_path: str):
    """[诊断] 用配置的 ASR 引擎转写一个 wav 文件，打印文本。验证模型可用。"""
    _setup_logging()
    from .asr.base import make_transcriber

    cfg = load_config()
    res = make_transcriber(cfg.asr).transcribe(wav_path)
    if not res.is_ok():
        typer.echo(f"转写失败：{res.error}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"✓ 转写成功（engine={res.value.engine}）：")
    typer.echo(res.value.text)


@app.command("serve")
def serve():
    """启动只读 Web 看板。"""
    import uvicorn

    cfg = load_config()
    uvicorn.run("ashare.web.app:create_app", factory=True,
                host=cfg.web.host, port=cfg.web.port)


@app.command("schedule")
def schedule():
    """启动定时轮询调度（前台阻塞运行）。"""
    _setup_logging()
    from .pipeline.scheduler import run_scheduler

    run_scheduler(_effective_config())   # 启动时合并 DB 设置；改设置后需重启调度


if __name__ == "__main__":
    app()
