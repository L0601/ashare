# 指令手册

> 本项目所有命令行操作集中在此文档。后续新增的指令也都记录到这里。

## 0. 前置依赖（只需一次）

```bash
# ffmpeg（音轨抽取必需）
brew install ffmpeg
```

## 1. 安装（首次部署）

```bash
cd /Users/ltc/Desktop/ashare

# 创建并激活 Python 3.12 虚拟环境
/usr/local/bin/python3.12 -m venv .venv
source .venv/bin/activate

# 安装项目本体 + 基础依赖，并注册 `ashare` 命令（关键，缺这步会 command not found）
pip install -e .

# Intel Mac 语音识别：SenseVoice Small INT8 + sherpa-onnx
pip install -e ".[asr-sensevoice]"

# 下载约 230 MB 的本地 INT8 模型
mkdir -p models
curl -L -o /tmp/sensevoice.tar.bz2 \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
tar -xjf /tmp/sensevoice.tar.bz2 -C models

# 跑测试需要的依赖
pip install -e ".[dev]"
```

> 注意：每开一个新终端要先 `source .venv/bin/activate` 再用 `ashare` 命令。

## 2. 配置（首次部署）

```bash
# 生成本地配置（config.yaml 已被 .gitignore 忽略，安全存放 cookie / api_key）
cp config.example.yaml config.yaml

# 然后编辑 config.yaml，填写：
#   douyin.cookie          抖音网页版登录后的 Cookie
#   douyin.author_sec_uid  仅用于首次迁移第一个用户，之后在 Web 设置中管理
#   llm.base_url/api_key/model  兼容 OpenAI 的 LLM 服务（如 DeepSeek）
#   asr.engine             正式跑使用 sensevoice；开发期可用 fake
#   asr.chunk_seconds      长音频切片目标时长，Intel Mac 默认 30 秒
#   telegram.enabled/bot_token/chat_id  处理完成后推送 Telegram（也可在 Web 后台设置）
```

## 3. 日常运行

```bash
source .venv/bin/activate     # 每个新终端先激活

# 解析博主 sec_uid（传主页/分享链接，含 /user/<sec_uid>，或直接传 sec_uid）
ashare resolve-author "https://www.douyin.com/user/MS4wLjAB..."

# 立即跑一轮：按数据库顺序串行处理全部启用用户，共享一份 ASR 模型
ashare run-once

# 启动 Web 后台（默认 http://127.0.0.1:8000，仅本机）
#   - 列表每 2 秒自动刷新（仅读本地库，不碰抖音，无风控风险）
#   - 顶栏「拉取最新」按钮：后台异步执行 run_once(limit=最近N条)，单飞防重复
#   - 顶栏「设置」按钮：右侧抽屉，可改运行时配置（存数据库，保存即生效）：
#       全局：cookie / 最近N条(latest_count) / poll_interval_minutes
#       用户：sec_uid / 昵称 / 金融或通用类型 / 独立系统提示词 / 启用状态
#       telegram_enabled(开关) / telegram_bot_token / telegram_chat_id
#       新增用户时 sec_uid 支持粘主页链接校验；创建后不可修改，可停用、不删除
#       UA、LLM 密钥/端点、ASR 模型等固化项不在后台，改 config.yaml
#   - 列表/详情显示作者名；视频处理完成后自动推送 Telegram（NOTIFY 阶段，幂等不重复）
#   - 每行「删除」按钮：删该视频全部记录+中间文件（居中确认弹窗）
#   - 设置抽屉「清空所有数据」：清空所有视频数据+中间文件，但保留配置（确认弹窗）
#   - 设置抽屉「自动拉取」开关：开启后，看板打开期间每隔「轮询间隔」分钟由前端自动触发拉取；
#     顶栏会显示「自动拉取·每N分钟」状态。关闭页面即停（仅在看板打开时生效）。
ashare serve

# 7x24 无人值守的定时轮询（独立后台进程，不依赖看板是否打开；默认每 90 分钟，前台阻塞，Ctrl+C 停止）
ashare schedule
```

> 看板「自动拉取」开关 vs `ashare schedule`：前者靠前端定时、仅看板打开时生效；
> 后者是独立进程、关浏览器也在跑。两者都用同一个「轮询间隔」配置。

## 3.5 逐段验证（首次运行强烈建议按此顺序）

> 不要一上来就 `run-once`。先逐段单独验证，出错立刻知道是哪一环。

```bash
source .venv/bin/activate

# 步骤1：验证观点分析（优先 ~/clean-codex，失败回退 llm.*；不联网抖音、不跑 ASR）
ashare check-llm [--user-id ID]

# 步骤2：验证抖音抓取（需 douyin.cookie + author_sec_uid；只读列表，不下载）
ashare resolve-author "https://www.douyin.com/user/MS4wLjAB..."   # 拿到 sec_uid 回填 config.yaml
ashare list-videos --count 5 [--user-id ID]

# 步骤2.5：验证单视频"解析+下载"（只需 douyin.cookie + 一个视频链接，不跑 ASR/LLM）
ashare download-one "https://www.douyin.com/video/74123..."          # 仅下载 mp4
ashare download-one "https://www.douyin.com/video/74123..." --audio  # 下载并 ffmpeg 抽音轨

# 步骤4：验证 ASR（CPU INT8；需先下载 SenseVoice 模型）
#   config.yaml 的 asr.engine 使用 sensevoice
pip install -e ".[asr-sensevoice]"
ashare check-asr data/videos/<YYYY-MM-DD>_<某个aweme_id>/audio.wav

# 步骤3+5：下载、抽音轨、转写、抽取 串起来一次跑完
ashare run-once
```

> 长音频会自动在 30 秒附近的低音量位置切片，并复用同一模型逐段识别。
> 本机实测 14 分 48 秒音频由优化前约 894 秒降至约 42 秒。

## 3.6 重置数据

```bash
ashare reset-db            # 清空视频分析数据，保留用户、提示词和全局设置
ashare reset-db --files    # 另删 data/videos 下已下载的视频/音频等中间文件
```

## 4. 测试

```bash
source .venv/bin/activate
pytest                 # 全部核心逻辑单测
pytest -q              # 精简输出
pytest tests/test_pipeline.py   # 只跑某个文件
```

## 5. 排错速查

```bash
# zsh: command not found: ashare
#   -> 没装包或没激活环境。执行：source .venv/bin/activate && pip install -e .

# 查看后台 serve 的日志（若用 nohup 启动）
tail -f /tmp/ashare_serve.log

# 指定使用某个配置文件运行（默认读项目根 config.yaml）
ASHARE_CONFIG=/path/to/config.yaml ashare serve
```
