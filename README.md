# 抖音多用户内容分析流水线

跟踪多个抖音博主的最新视频，并按用户选择「金融分析」或「通用分析」，自动完成：

```
发现最新视频 → 下载无水印 mp4 → ffmpeg 抽音轨(16k 单声道)
  → 本地 SenseVoice Small INT8 转写 → Codex Agent 优先分析（失败回退 LLM）
  → 推送 Telegram / 企业微信
```

全程留痕（视频链接、标题、抽取时间、转写文字、AI 输入/输出、各阶段状态），数据存
SQLite，中间文件按视频分目录存放，并提供 **Web 后台**（看板查看进度/结果 + 设置抽屉改运行时
配置 + 一键拉取 + 单条/全部删除），支持定时低频轮询以获取新视频且尽量规避抖音风控。

## 架构

| 模块 | 职责 |
|------|------|
| `src/ashare/config.py` | 配置加载（pydantic 校验 yaml）+ DB 运行时覆盖（`apply_overrides`），集中敏感/可变项 |
| `src/ashare/db.py` | SQLite 持久化（tracked_users / videos / transcripts / analyses / opinions / settings 等） |
| `src/ashare/douyin/` | 抓取层：F2 封装（`client.py`，含 sec_uid/profile 解析）+ 无水印下载（`downloader.py`），抽象接口 + Fake |
| `src/ashare/media/audio.py` | ffmpeg 抽音轨（命令构造与执行分离） |
| `src/ashare/asr/` | 语音转写：SenseVoice INT8（Intel CPU）+ 旧 Qwen 后端 + Fake |
| `src/ashare/llm/` | 观点抽取：本机 Codex CLI Agent 优先 + 兼容 OpenAI LLM 兜底 + 防御性 JSON 解析 |
| `src/ashare/notify/` | 推送层：中立通知载荷 + Telegram / 企业微信群机器人渲染与发送 + Fake/Noop |
| `src/ashare/pipeline/` | 单用户流水线 + 多用户串行协调（共享一份 ASR）+ 定时调度 |
| `src/ashare/web/` | Web 后台：FastAPI 接口（`app.py`）+ 数据装配（`service.py`）+ 后台单飞拉取（`pull.py`）+ 自包含单页（`templates/dashboard.html`） |
| `src/ashare/cli.py` | 命令行入口（运行 / 调度 / 诊断 / 重置） |

设计原则：抓取/ASR/LLM 都抽象成接口并依赖注入，算法与 IO 分离，可恢复错误用 `Result`
表达，核心逻辑全部配有单测（用 fake 依赖，不联网、不碰真实模型）。

## 安装

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .                 # 基础依赖（抓取/LLM/Web/调度）
pip install -e ".[asr-sensevoice]"   # Intel Mac：CPU INT8
pip install -e ".[dev]"              # 跑测试用
```

需预装 `ffmpeg`（`brew install ffmpeg`）。SenseVoice 模型需下载一次：

```bash
mkdir -p models
curl -L -o /tmp/sensevoice.tar.bz2 \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
tar -xjf /tmp/sensevoice.tar.bz2 -C models
```

## 配置

```bash
cp config.example.yaml config.yaml   # config.yaml 已被 .gitignore 忽略
```

填写关键项：
- `douyin.cookie`：抖音网页版登录后的 Cookie（公开内容理论可游客访问，但带登录 Cookie 显著降低触发风控概率）
- `douyin.author_sec_uid` / `author_name`：仅用于首次启动迁移第一个用户；之后在 Web「设置」中管理用户
- `llm.base_url` / `llm.api_key` / `llm.model`：兼容 OpenAI 的服务（如 DeepSeek）
- `asr.engine`：Intel Mac 使用 `sensevoice`；`fake` 仅用于测试
- `asr.model`：SenseVoice 模型目录或 `model.int8.onnx` 文件路径
- `asr.num_threads`：CPU 推理线程数，当前四核 Intel Mac 使用 `4`
- `asr.chunk_seconds`：长音频切片目标时长，默认 `30` 秒；会在附近低音量位置切分
- `asr.use_itn`：是否开启逆文本归一化，默认开启
- `douyin.user_agent`：默认桌面浏览器 UA（已与 web 接口/cookie 来源一致），一般无需改
- `telegram.enabled` / `telegram.bot_token` / `telegram.chat_id`：视频处理完成后推送到 Telegram（群 id 为负数）
- `wecom.enabled` / `wecom.webhook_url`：视频处理完成后推送到企业微信群机器人；webhook 只写本地配置，禁止提交 Git

敏感信息只存在 `config.yaml`，不入 git。

观点分析会自动探测 `~/clean-codex` 和本机 `codex` 命令；两者可用时优先通过
`CODEX_HOME=~/clean-codex codex exec` 分析转录文字。每条视频使用隔离、只读、无会话留存的临时执行，
并用 JSON Schema 约束输出。Codex 缺失、未登录、超时、执行失败或输出不合法时，自动回退到上述
`llm.*` 服务。这里的“本机 Codex”指在本机启动 Agent，推理仍使用该目录登录的 ChatGPT 云端账号，
不是离线模型。

**运行时配置（Web 后台可改，存数据库覆盖文件）**：`cookie`、`latest_count`、
`poll_interval_minutes`、`telegram_enabled` / `telegram_bot_token` / `telegram_chat_id`、`wecom_enabled`
这几项常改的全局配置可在看板「设置」抽屉里修改，保存即生效。
监控用户单独存于 `tracked_users` 表，每个用户绑定内容类型、系统提示词和启用状态；内置金融/通用两套默认提示词。
切换内容类型不会覆盖已有自定义提示词，只有点击“恢复当前类型默认提示词”才会替换。
存于 SQLite `settings` 表并覆盖文件默认值（"清空所有数据"不会清掉它们）。UA、LLM 密钥/端点、
ASR 模型路径、企业微信 webhook 等固化/敏感项不进后台，仍改 `config.yaml`；修改文件配置后需重启进程。

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
ashare check-llm [--user-id ID]        # 按指定/第一个用户验证 Codex 优先 + LLM 回退
ashare list-videos --count 5 [--user-id ID]  # 按指定/第一个用户验证抓取
ashare download-one "<视频链接>" --audio  # 验解析+下载+抽音轨（不跑 ASR/LLM）
ashare check-asr data/videos/<YYYY-MM-DD>_<id>/audio.wav   # 验 SenseVoice INT8 转写
ashare reset-db [--files]              # 清空视频分析数据，保留用户与配置
```

**Web 后台功能**（`ashare serve` 后访问）：顶部用户下拉只过滤该用户的视频和统计，默认第一个用户；
「拉取最新」、浏览器自动拉取、CLI 和定时任务都会按数据库顺序串行处理全部启用用户；
设置抽屉可新增、编辑、停用用户并配置其内容类型和系统提示词（sec_uid 创建后不可修改、不提供删除）；
详情按金融/通用结构展示，并保留分析时的类型和提示词快照。清空视频数据不会删除用户及其提示词。

> 用户给的「搜索结果页」链接不稳定，推荐用博主主页链接（含 `/user/<sec_uid>`）解析出
> sec_uid 后，走更稳定的博主主页发布接口。

## 防风控

- 轮询间隔默认 90 分钟（`schedule.poll_interval_minutes`，建议活跃博主 90~120 分钟）
- 每次网络请求前有「基础间隔 + 随机抖动」错峰（`request_min_seconds` / `request_jitter_seconds`）
- **桌面浏览器 UA** + 登录 Cookie（UA / cookie 来源 / web 接口 / 浏览器指纹保持一致，降低风控误判）；不频繁更换 UA
- 抓取失败（含风控/签名失效）按可恢复错误记录到 `poll_runs`，不硬刚重试
- 抓取层会多取并按发布时间倒序，规避置顶视频占用「最新 N 条」名额

## 多渠道推送

视频**完整处理完成**（转写 + 分析）后，NOTIFY 阶段把结果推送到全部启用渠道。金融消息含板块和逐条观点；
通用消息含摘要、标签、正文和口播全文。Telegram 使用 HTML，企业微信使用 Markdown，超长内容
按各渠道限制自动拆条。企业微信按 UTF-8 字节拆分并限制请求速率，避免超过单 webhook 20 条/分钟。

- 配置 `telegram.enabled/bot_token/chat_id`（或在 Web 后台「设置」里填）即生效。
- 企业微信真实地址仅配置在 `wecom.webhook_url`；Web 后台只提供 `wecom_enabled` 开关，不读取或返回地址。
- Telegram 与企业微信可同时启用。系统按视频、渠道记录投递状态；一个渠道失败时仍会尝试其他渠道，
  下次只重试失败渠道。全部启用渠道成功后，NOTIFY 阶段才成功。
- **历史不回溯**：开启推送前已完成的老视频不会被补发（它们不会被重新处理）。
- webhook URL 含访问密钥，不会写入日志；请勿提交到 GitHub、博客或其他公开位置。

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
- SenseVoice 模型下载后完全离线推理；真实模型推理未纳入单测，单测覆盖可离线验证的装配与逻辑。
- SenseVoice 会把长音频按约 30 秒切片并复用模型，避免整段输入导致推理时间急剧增加。
