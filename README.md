# MarsDog Voice Interaction

MarsDog 的独立语音交互 ROS2 包，负责从唤醒到意图事件发布的完整语音链路：

```text
讯飞唤醒板
    ↓
麦克风采集 → Silero VAD ─┬→ 流式 KWS → 即时动作事件
                          ├→ Paraformer ASR → 规则 / RKLLM 意图 → 最终事件
                          └→ 3D-Speaker 声纹识别 → 身份事件
                                                ↓
                                  /perception/audio_event
```

项目只负责“听见了什么”以及语音会话的生命周期，不订阅视觉数据，不直接控制
底盘，也不负责行为优先级、排队或抢占。

## 主要能力

- 通过讯飞串口模块唤醒并获取声源角度。
- 使用 sherpa-onnx Silero VAD 实时切分语音。
- VAD 与流式 KWS 复用同一份 16 kHz 麦克风数据，不重复打开设备。
- KWS 命中中英文动作词后立即发布事件，降低动作响应延迟。
- 使用 Paraformer 完成整句 ASR，再由规则或 RKLLM 输出结构化意图。
- 支持说话人识别、录制注册、WAV 上传注册及声纹管理。
- 使用 `interaction_id` 和 `utterance_id` 关联一次会话及其中的每句话。
- 支持 pipeline Mock 和直接事件 Mock，无硬件也可联调下游。

KWS 即时事件不会阻止后续 ASR、声纹和意图处理；如果最终意图与同一句已经
发布的 KWS 事件相同，节点会自动去重。

## 项目边界

| 项目 | 职责 |
|---|---|
| 本项目 | 唤醒、录音、VAD、KWS、ASR、声纹、意图和语音会话事件 |
| 视觉项目 | 人脸、目标检测和视觉事件 |
| 行为树项目 | 候选行为、优先级、仲裁和抢占 |
| 动作项目 | 跟随、姿态动作和底盘执行 |

跨项目交接语义见 [docs/HANDOFF.md](docs/HANDOFF.md)，完整 ROS2 消息字段和
Service 参数见 [docs/ROS2_CONTRACT.md](docs/ROS2_CONTRACT.md)。

## 运行环境

- Ubuntu 22.04 / ROS2 Humble
- Python 3.10
- [uv](https://docs.astral.sh/uv/)
- 麦克风及 PortAudio；无法使用 `sounddevice` 时会回退到 `arecord`
- 讯飞离线语音唤醒模块，默认串口 `/dev/ttyACM0`
- RK3588 平台及 RKLLM 运行库（仅启用 `intent_llm` 时需要）

Python 依赖由 `pyproject.toml` 和 `uv.lock` 管理：

```bash
cd /home/cat/xbb/MarsDogVoiceInteraction
uv sync --extra dev
```

推理模型不提交到仓库，默认统一存放在 `/home/cat/xbb/models`：

| 模块 | 默认路径 |
|---|---|
| VAD | `vad/silero_vad.onnx` |
| KWS | `wakeup/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/` |
| ASR | `asr/sherpa-onnx-paraformer-zh-2024-03-09/` |
| Speaker | `speaker/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx` |
| RKLLM | `llm/qwen2_5_5b_rk3588_260722_w8a8.rkllm` |

部署到其他目录时，需要同步修改 `config/voice.yaml` 中的模型、关键词、存储目录
和 RKLLM 运行库路径。

## 构建

本仓库已软链接到 ROS2 工作空间：

```text
/home/cat/ros2_ws/src/marsdog_voice_interaction
    -> /home/cat/xbb/MarsDogVoiceInteraction
```

在工作空间中构建：

```bash
source /opt/ros/humble/setup.bash
cd /home/cat/ros2_ws
colcon build --packages-select marsdog_voice_interaction --symlink-install
source install/setup.bash
```

必须经过 colcon 构建后才能生成并使用自定义的 `VoiceTask.srv`。

## 启动

### ROS2 Launch（推荐）

```bash
source /opt/ros/humble/setup.bash
source /home/cat/ros2_ws/install/setup.bash
ros2 launch marsdog_voice_interaction voice.launch.py
```

指定另一份配置：

```bash
ros2 launch marsdog_voice_interaction voice.launch.py \
  config_path:=/absolute/path/to/voice.yaml
```

### 直接运行源码入口

适合开发调试，但调用 `/perception/voice/task` 前仍需 source 已构建的工作空间：

```bash
cd /home/cat/xbb/MarsDogVoiceInteraction
source /opt/ros/humble/setup.bash
source /home/cat/ros2_ws/install/setup.bash
uv run marsdog-voice-interaction \
  --ros-args -p config_path:="$PWD/config/voice.yaml"
```

## 配置

主配置文件为 [`config/voice.yaml`](config/voice.yaml)。

| 配置段 | 作用 |
|---|---|
| `logging` | 日志级别和输出目录 |
| `mock` | Mock 开关、模式和事件间隔 |
| `storage.root` | 声纹注册表、样本及临时数据目录 |
| `topics` | ROS2 Topic 和 Service 名称 |
| `interaction.idle_timeout_sec` | 最后一次有效语音后等待多久结束会话 |
| `providers.wakeup` | 讯飞串口和唤醒事件类型 |
| `providers.audio` | 麦克风、VAD 阈值、语音时长和预录缓存 |
| `providers.kws` | KWS 模型、关键词及检测阈值 |
| `providers.asr` | ASR 模型、语言和 ITN |
| `providers.speaker` | 声纹模型及匹配阈值 |
| `providers.intent_rule` | 规则意图回退 |
| `providers.intent_llm` | RKLLM 模型和生成参数 |

与收音质量直接相关的参数：

| 参数 | 当前值 | 说明 |
|---|---:|---|
| `vad_threshold` | `0.5` | 越低越容易检测轻声，也越容易受噪声影响 |
| `min_speech_dur` | `0.25` 秒 | 低于此时长的声音不作为有效语音 |
| `min_silence_dur` | `0.5` 秒 | 句尾持续静音达到该时长后结束切分 |
| `pre_roll_sec` | `0.3` 秒 | 补回 VAD 触发前的音频，防止漏掉句首 |
| `max_duration_sec` | `8.0` 秒 | 单轮录音的最长等待/采集时间 |

调节 VAD 阈值前，应先检查系统麦克风输入设备和硬件增益。阈值过低会把风扇、
碰撞声等环境噪声误判为语音。

## ROS2 接口

| 方向 | 名称 | 类型 | 说明 |
|---|---|---|---|
| 发布 | `/perception/audio_event` | `std_msgs/msg/String` | JSON 语音、身份、指令及会话状态事件 |
| 发布 | `/perception/voice/enrollment_event` | `std_msgs/msg/String` | JSON 声纹注册进度事件 |
| 提供 | `/perception/voice/task` | `marsdog_voice_interaction/srv/VoiceTask` | 监听和声纹管理任务 |

支持的 `task_type`：

- `start_listening`、`stop_listening`
- `start_speaker_enrollment`、`cancel_speaker_enrollment`
- `upload_speaker`、`verify_speaker`
- `list_speakers`、`delete_speaker`

接口发现与监听：

```bash
ros2 topic info -v /perception/audio_event
ros2 topic echo /perception/audio_event
ros2 topic echo /perception/voice/enrollment_event
ros2 service type /perception/voice/task
ros2 interface show marsdog_voice_interaction/srv/VoiceTask
```

手动开始和停止监听：

```bash
ros2 service call /perception/voice/task \
  marsdog_voice_interaction/srv/VoiceTask \
  "{task_id: manual-start, task_type: start_listening, params_json: '{}'}"

ros2 service call /perception/voice/task \
  marsdog_voice_interaction/srv/VoiceTask \
  "{task_id: manual-stop, task_type: stop_listening, params_json: '{}'}"
```

## Mock 联调

仓库提供 [`config/voice.mock.yaml`](config/voice.mock.yaml)，无需串口、麦克风和
模型即可直接发布模拟事件：

```bash
source /opt/ros/humble/setup.bash
source /home/cat/ros2_ws/install/setup.bash
ros2 launch marsdog_voice_interaction voice.launch.py \
  config_path:=/home/cat/xbb/MarsDogVoiceInteraction/config/voice.mock.yaml
```

Mock 有两种模式：

- `mock.mode: event`：绕过全部上游 Provider，直接生成语义完整的音频事件，适合
  测试行为树等下游消费者。
- `mock.mode: pipeline`：使用 Mock Wakeup、VAD、ASR 和 Speaker 走完整节点流程，
  适合验证状态机和 Provider 编排。

`voice.mock.yaml` 默认使用 `event` 模式。

## 测试

```bash
cd /home/cat/xbb/MarsDogVoiceInteraction
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q marsdog_voice_interaction tests
```

完成代码或接口修改后，建议再执行一次 ROS2 构建：

```bash
source /opt/ros/humble/setup.bash
cd /home/cat/ros2_ws
colcon build --packages-select marsdog_voice_interaction --symlink-install
```

## 常见问题

### 说话需要很大声，或识别结果缺少开头

1. 检查系统默认麦克风是否正确，以及输入增益是否过低。
2. 观察启动日志中的录音后端、VAD 阈值和检测时长。
3. 适度降低 `vad_threshold`，但不要一次降得过多。
4. 确认 `pre_roll_sec` 大于 `0`，用于保留 VAD 触发前的句首。

### 找不到 `/perception/voice/task` 或 `VoiceTask` 类型

自定义 Service 尚未生成，重新执行 colcon 构建并 source 工作空间：

```bash
source /opt/ros/humble/setup.bash
cd /home/cat/ros2_ws
colcon build --packages-select marsdog_voice_interaction --symlink-install
source install/setup.bash
```

### 启动的不是当前源码版本

先确认 ROS2 实际解析到的安装前缀：

```bash
ros2 pkg prefix --share marsdog_voice_interaction
```

预期结果为：

```text
/home/cat/ros2_ws/install/marsdog_voice_interaction/share/marsdog_voice_interaction
```

若结果来自其他工作空间，请打开一个干净终端，只 source ROS2 Humble 和当前
`/home/cat/ros2_ws/install/setup.bash`。

### 串口无法打开

确认设备存在、端口配置正确，并检查当前用户是否有串口访问权限：

```bash
ls -l /dev/ttyACM0
```

### `sounddevice` 无法打开麦克风

节点会记录 PortAudio 错误并尝试回退到 `arecord`。可先用系统工具确认设备：

```bash
arecord -l
```

如需固定输入设备，在 `providers.audio.config.device` 中配置设备名称或索引。

## 目录结构

```text
MarsDogVoiceInteraction/
├── config/                         # 正式配置、Mock 配置和 KWS 关键词
├── data/                           # 声纹注册表与样本
├── docs/                           # 交接说明、迁移记录和 ROS2 契约
├── launch/                         # ROS2 launch 文件
├── lib/                            # RKLLM 运行库
├── marsdog_voice_interaction/
│   ├── adapters/                   # RKLLM、讯飞串口底层适配
│   ├── core/                       # 会话状态机、去重和声纹注册管理
│   ├── messages/                   # 事件结构、事件类型和意图协议
│   ├── nodes/                      # ROS2 主节点
│   ├── providers/                  # Wakeup/VAD/KWS/ASR/Speaker/Intent
│   └── utils/                      # 配置、日志、时间和 ROS 入口工具
├── srv/VoiceTask.srv               # 语音任务 Service
└── tests/                          # 契约、KWS 和会话恢复测试
```

## 开发约束

- `EVT_VOICE_CALL_NAME` 到终止 `EVT_STATE_CHANGED` 必须保持同一个
  `interaction_id`。
- 每句话创建新的 `utterance_id`；同句话的 KWS、声纹、speech 和最终意图共享
  该 ID。
- 只有非空 ASR 或有效 KWS 才刷新会话的最后有效语音时间。
- 活跃 VAD 采集不能被会话静默超时截断。
- 新增命令时应同步修改事件类型、意图映射、测试、ROS2 契约以及下游行为映射。
- 下游应按 `interaction_id + utterance_id + event_type` 做幂等保护。

## 延伸文档

- [项目交接与上下游语义](docs/HANDOFF.md)
- [ROS2 消息和 Service 完整契约](docs/ROS2_CONTRACT.md)
- [旧项目迁移说明](docs/MIGRATION.md)
