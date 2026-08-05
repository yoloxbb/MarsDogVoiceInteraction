# MarsDog Voice Interaction

> 项目交接与上下游对接请先阅读 [docs/HANDOFF.md](docs/HANDOFF.md)；完整消息字段见
> [docs/ROS2_CONTRACT.md](docs/ROS2_CONTRACT.md)。

独立的 MarsDog 语音交互 Python/ROS2 项目，负责唤醒、VAD、ASR、声纹、意图、
语音会话和 `/perception/audio_event`。本项目不导入视觉项目，也不访问摄像头。

对话录音期间，流式 KWS 与 VAD 复用同一份麦克风分块。KWS 命中中英文动作
命令后会立即发布动作事件；整句结束后仍执行 ASR、LLM/规则意图。如果最终
动作事件与本句已发布的 KWS 事件相同，则只保留即时事件；不同事件仍会发布。

## 环境

- Python 3.10
- uv
- ROS2 Humble

推理模型继续统一放在 `/home/cat/xbb/models`，避免在多个项目中复制大文件；
项目自己的配置、声纹注册表、声纹样本和 RKLLM 运行库已随语音项目迁移。

默认 KWS 模型目录：
`/home/cat/xbb/models/wakeup/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20`。
命令词原文和模型 token 文件分别位于
`config/kws_keywords_raw.txt`、`config/kws_keywords.txt`。

```bash
cd /home/cat/xbb/MarsDogVoiceInteraction
uv sync --extra dev
source /opt/ros/humble/setup.bash
uv run pytest
```

直接运行源码节点：

```bash
source /opt/ros/humble/setup.bash
uv run marsdog-voice-interaction \
  --ros-args -p config_path:=config/voice.yaml
```

ROS2 构建：

```bash
source /opt/ros/humble/setup.bash
cd /home/cat/xbb/MarsDogVoiceInteraction
colcon build --base-paths . --packages-select marsdog_voice_interaction
source install/setup.bash
ros2 launch marsdog_voice_interaction voice.launch.py
```

设置 `mock.enabled: true`、`mock.mode: event` 可直接模拟下游语音事件，不加载硬件和模型。

## ROS2 接口

- 发布：`/perception/audio_event`
- 发布：`/perception/voice/enrollment_event`
- Service：`/perception/voice/task`

语音任务：`start_speaker_enrollment`、`cancel_speaker_enrollment`、
`upload_speaker`、`verify_speaker`、`list_speakers`、`delete_speaker`、
`start_listening`、`stop_listening`。

完整字段约定见 [docs/ROS2_CONTRACT.md](docs/ROS2_CONTRACT.md)。
