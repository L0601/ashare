# 抖音财经博主金融观点抽取流水线

跟踪指定抖音财经博主（如「创业魔法师」）的最新视频，自动完成：

```
发现最新视频 → 下载无水印 mp4 → ffmpeg 抽音轨(16k 单声道)
  → 本地 Qwen3-ASR-1.7B 转写 → 云端 LLM 抽取行业/个股观点
```

全程留痕（视频链接、标题、抽取时间、转写文字、AI 输入/输出、各阶段状态），数据存
SQLite，中间文件按视频分目录存放，并提供 **Web 后台**（看板查看进度/结果 + 设置抽屉改运行时
配置 + 一键拉取 + 单条/全部删除），支持定时低频轮询以获取新视频且尽量规避抖音风控。

## 架构

| 模块 | 职责 |
|------|------|
| `src/ashare/config.py` | 配置加载（pydantic 校验 yaml）+ DB 运行时覆盖（`apply_overrides`），集中敏感/可变项 |
| `src/ashare/db.py` | SQLite 持久化（videos / stage_runs / transcripts / analyses / opinions / poll_runs / settings） |
| `src/ashare/douyin/` | 抓取层：F2 封装（`client.py`，含 sec_uid/profile 解析）+ 无水印下载（`downloader.py`），抽象接口 + Fake |
| `src/ashare/media/audio.py` | ffmpeg 抽音轨（命令构造与执行分离） |
| `src/ashare/asr/` | 语音转写：接口 + Qwen3-ASR（MLX/transformers）+ Fake |
| `src/ashare/llm/` | LLM 抽取：prompt + 兼容 OpenAI 调用 + 防御性 JSON 解析 |
| `src/ashare/pipeline/` | 流水线状态机（`runner.py`，幂等续跑）+ 定时调度（`scheduler.py`） |
| `src/ashare/web/` | Web 后台：FastAPI 接口（`app.py`）+ 数据装配（`service.py`）+ 后台单飞拉取（`pull.py`）+ 自包含单页（`templates/dashboard.html`） |
| `src/ashare/cli.py` | 命令行入口（运行 / 调度 / 诊断 / 重置） |

设计原则：抓取/ASR/LLM 都抽象成接口并依赖注入，算法与 IO 分离，可恢复错误用 `Result`
表达，核心逻辑全部配有单测（用 fake 依赖，不联网、不碰真实模型）。

## 安装

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                 # 基础依赖（抓取/LLM/Web/调度）
# 语音识别后端二选一（Apple Silicon 推荐 mlx）：
pip install -e ".[asr-mlx]"          # MLX，M 系列芯片最优
pip install -e ".[asr-transformers]" # transformers + MPS/CPU，兜底
pip install -e ".[dev]"              # 跑测试用
```

需预装 `ffmpeg`（`brew install ffmpeg`）。Qwen3-ASR-1.7B 权重首次运行会自动下载（数 GB）。

## 配置

```bash
cp config.example.yaml config.yaml   # config.yaml 已被 .gitignore 忽略
```

填写关键项：
- `douyin.cookie`：抖音网页版登录后的 Cookie（公开内容理论可游客访问，但带登录 Cookie 显著降低触发风控概率）
- `douyin.author_sec_uid`：博主 sec_uid，可用下方 `resolve-author` 解析后回填
- `llm.base_url` / `llm.api_key` / `llm.model`：兼容 OpenAI 的服务（如 DeepSeek）
- `asr.engine`：`mlx` / `transformers` / `fake`（开发期可用 `fake` 跑通链路）
- `asr.model`：模型名或本地路径（如 `mlx-community/Qwen3-ASR-1.7B-8bit`，或已下载到本地的目录）
- `douyin.user_agent`：默认桌面浏览器 UA（已与 web 接口/cookie 来源一致），一般无需改

敏感信息只存在 `config.yaml`，不入 git。

**运行时配置（Web 后台可改，存数据库覆盖文件）**：`cookie`、`author_sec_uid`、`latest_count`、
`poll_interval_minutes` 这几项常改的配置可在看板「设置」抽屉里修改，保存即生效，
存于 SQLite `settings` 表并覆盖文件默认值（"清空所有数据"不会清掉它们）。UA、LLM 密钥/端点、
ASR 模型路径等固化项不进后台，仍改 `config.yaml`。

## 使用

```bash
# 1) 解析博主 sec_uid（传主页/分享链接、视频链接或直接 sec_uid），回填到 config.yaml
ashare resolve-author "https://www.douyin.com/user/MS4wLjAB..."

# 2) 立即跑一轮：发现新视频 + 处理未完成视频（--limit 限本轮处理条数）
ashare run-once [--limit N]

# 3) 启动 Web 后台（默认 http://127.0.0.1:8000）
ashare serve

# 4) 启动定时轮询（独立后台进程，7x24，前台阻塞运行）
ashare schedule
```

**诊断命令**（首次部署逐段验证，详见 `COMMANDS.md`）：

```bash
ashare check-llm                       # 验 LLM 连通（内置样例，不联网抖音）
ashare list-videos --count 5           # 验抓取：打印最新视频列表（只读不下载）
ashare download-one "<视频链接>" --audio  # 验解析+下载+抽音轨（不跑 ASR/LLM）
ashare check-asr data/videos/<id>/audio.wav   # 验 Qwen3-ASR 转写
ashare reset-db [--files]              # 重置：清空数据（--files 连带删中间文件，保留配置）
```

**Web 后台功能**（`ashare serve` 后访问）：看板每 2s 自动刷新、阶段徽章/观点表/转写/AI 留痕详情抽屉；
「拉取最新」单飞后台拉取并处理最新 N 条；「自动拉取」开关（前端定时驱动，关页即停）；
「设置」抽屉改运行时配置（sec_uid 可粘主页链接联网校验账号）；单条删除 / 清空所有数据（确认弹窗）。

> 用户给的「搜索结果页」链接不稳定，推荐用博主主页链接（含 `/user/<sec_uid>`）解析出
> sec_uid 后，走更稳定的博主主页发布接口。

## 防风控

- 轮询间隔默认 90 分钟（`schedule.poll_interval_minutes`，建议活跃博主 90~120 分钟）
- 每次网络请求前有「基础间隔 + 随机抖动」错峰（`request_min_seconds` / `request_jitter_seconds`）
- **桌面浏览器 UA** + 登录 Cookie（UA / cookie 来源 / web 接口 / 浏览器指纹保持一致，降低风控误判）；不频繁更换 UA
- 抓取失败（含风控/签名失效）按可恢复错误记录到 `poll_runs`，不硬刚重试
- 抓取层会多取并按发布时间倒序，规避置顶视频占用「最新 N 条」名额

## 续跑与幂等

每个视频的每个阶段状态都落库；已成功的阶段重跑时跳过，中途失败下次从失败阶段继续。
按 `aweme_id` 幂等，重复运行不会重复下载/分析已完成的视频。

## 测试

```bash
pytest        # 全部核心逻辑单测（db / 抓取解析 / 下载重试 / 音轨命令 / LLM 解析 / 流水线状态机 / 看板装配）
```

## 已知限制

- F2 依赖抖音 Web 签名（X-Bogus），抖音更新算法时可能临时失效；抓取层已抽象成接口，
  必要时可切换到浏览器自动化方案（Playwright/MediaCrawler）。
- Cookie 会过期，需定期手动更新到 `config.yaml`。
- 真实抓取与真实 ASR 推理需联网/模型权重，未纳入单测；单测覆盖的是可离线验证的纯逻辑。
