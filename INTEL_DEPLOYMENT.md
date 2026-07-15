# Intel Mac 适配与安装总结

## 1. 改造目标

本项目原先运行在 Apple Silicon Mac Pro M4 上，并使用本地 Qwen ASR
模型。本次改造目标是让程序在当前 Intel Mac 上稳定、高效地运行，同时
保持抖音抓取、音频处理、LLM 分析、Web 看板和定时任务等原有功能不变。

当前开发分支：`intel`

## 2. 当前电脑配置

- 机型：MacBook Pro 16,2
- CPU：Intel Core i5 2.0 GHz
- CPU 核心：4 核 / 8 线程
- 内存：16 GB
- GPU：Intel Iris Plus Graphics
- GPU 显存：动态共享，最大约 1536 MB
- 系统：macOS 15.0.1
- 架构：x86_64

## 3. ASR 技术选型

最终采用：

- 模型：SenseVoice Small INT8
- 推理框架：sherpa-onnx 1.13.4
- 运行设备：CPU
- 默认线程数：4
- 长音频切片：约 30 秒，并在附近低音量位置选择边界
- 默认语言：中文
- ITN：启用

模型目录：

```text
models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17
```

选择该方案的原因：

1. 当前 Intel Iris Plus Graphics 不适合本项目的 ASR 推理，PyTorch MPS
   在 Intel Mac 上不可用。
2. SenseVoice Small INT8 内存占用和计算量明显低于 Qwen ASR 1.7B，
   更适合 16 GB 内存和四核 Intel CPU。
3. sherpa-onnx 原生支持 macOS x86_64，CPU 推理稳定，无需 CUDA。
4. 2024-07-17 模型支持中、英、日、韩、粤语，并保留中文标点能力。

## 4. Python 与依赖环境

- Homebrew Python：3.12.13
- Pip：26.1.2
- 虚拟环境：`.venv`
- setuptools：83.0.0
- wheel：0.47.0
- sherpa-onnx：1.13.4
- soundfile：0.14.0

项目采用虚拟环境安装，没有替换 macOS 自带的系统 Python。

程序已用可编辑模式安装：

```zsh
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
```

依赖检查结果：

```text
No broken requirements found.
```

## 5. FFmpeg 处理方案

- FFmpeg 版本：8.1.2
- 安装来源：Homebrew
- 当前可用硬件加速接口：VideoToolbox

项目只从视频中抽取音轨，并通过 `-vn` 跳过视频解码，因此 Intel GPU
对这一步没有明显收益。当前继续使用 FFmpeg 的 CPU/系统音频组件是更
合适且稳定的方案。

真实音频抽取测试已经通过。

## 6. 主要代码改造

1. 新增 `SenseVoiceTranscriber`，支持：
   - INT8 ONNX 模型
   - 模型延迟加载
   - 识别器复用
   - 可配置 CPU 线程数
   - 长音频静音附近切片
   - 单次加载模型后逐段复用
   - 中英文片段安全拼接
   - 中文等语言别名
   - ITN 开关
   - WAV 文件读取和异常封装
2. ASR 工厂新增 `sensevoice` 引擎，同时保留原有 MLX、
   Transformers 和 Fake 引擎。
3. 默认 ASR 配置切换为 SenseVoice Small INT8。
4. 增加 `asr-sensevoice` 可选依赖组。
5. 更新配置示例、README 和命令说明。
6. 新增 SenseVoice 切片、文本合并、模型复用和默认配置测试。

## 7. 测试与性能

完整测试结果：

```text
180 passed, 1 warning in 3.73s
```

该警告来自已有的 Starlette/httpx 弃用提示，与本次 ASR 改造无关。

使用官方中文测试音频进行实际识别：

- 音频时长：5.592 秒
- 首次冷启动：3.077 秒
- 模型加载后的热识别：0.231 秒
- 识别结果：`开放时间早上9点至下午5点。`
- 实际引擎：`sensevoice/sherpa-onnx-int8`

线程性能对比：

| CPU 线程数 | 识别耗时 |
| --- | ---: |
| 1 | 0.620 秒 |
| 2 | 0.354 秒 |
| 4 | 0.319 秒 |

因此默认使用 4 个 CPU 线程。

长音频真实优化测试：

| 项目 | 优化前 | 优化后 |
| --- | ---: | ---: |
| 音频时长 | 888 秒 | 888 秒 |
| FFmpeg 抽音轨 | 约 1.5 秒 | 约 1.5 秒 |
| SenseVoice 转写 | 894 秒 | 41.877 秒 |
| 切片数量 | 1 段整段输入 | 30 段 |

同一条 14 分 48 秒音频的转写速度提升约 21.3 倍。优化后的最短切片
为 15.502 秒，最长切片为 32.9 秒，最终得到 5647 个字符。

## 8. 使用方法

进入项目并激活环境：

```zsh
cd /Users/ltc/Desktop/ashare
source .venv/bin/activate
ashare --help
```

首次使用时创建配置文件：

```zsh
cp config.example.yaml config.yaml
```

然后编辑 `config.yaml`，填写：

- 抖音 Cookie
- 需要处理的用户或博主信息
- LLM/Codex 配置
- 其他业务参数

ASR 推荐配置：

```yaml
asr:
  engine: sensevoice
  model: models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17
  language: zh
  num_threads: 4
  chunk_seconds: 30
  use_itn: true
```

常用命令：

```zsh
ashare check-asr 音频文件.wav
ashare run-once
ashare schedule
ashare serve
```

## 9. 当前状态

- `intel` 分支已经创建并切换。
- Python 3.12 虚拟环境已经创建。
- SenseVoice Small INT8 模型已经下载。
- sherpa-onnx 和项目依赖已经安装。
- FFmpeg 已安装且音频抽取验证通过。
- 项目命令入口可正常运行。
- ASR 真实推理和完整测试均已通过。
- 14 分 48 秒长音频转写已从约 15 分钟优化到约 42 秒。
- 用户业务配置和凭据仍需写入本地 `config.yaml`。

至此，Intel Mac 的程序改造、环境安装和核心验证已经完成。
