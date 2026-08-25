# 语音项目交接说明

> 对接基线：2026-08-04 / 多项目契约 1.0.0

## 1. 本项目负责什么

语音节点负责唤醒、录音/VAD、流式 KWS、ASR、声纹识别、意图分类以及一次语音会话的生命周期。它只发布“听见了什么”和“会话状态”，不订阅视觉数据，也不直接发布 `/cmd_vel` 或调用动作系统。

- 主节点：`voice_interaction`
- 入口：`marsdog-voice-interaction`
- 配置：`config/voice.yaml`

## 2. 对外接口

| 方向 | 接口 | 类型 | QoS/说明 |
|---|---|---|---|
| 发布 | `/perception/audio_event` | `std_msgs/msg/String` JSON | RELIABLE, KEEP_LAST 10 |
| 发布 | `/perception/voice/enrollment_event` | `std_msgs/msg/String` JSON | RELIABLE, KEEP_LAST 10 |
| 提供 | `/perception/voice/task` | `marsdog_voice_interaction/srv/VoiceTask` | 管理声纹和监听状态 |

完整 JSON 字段和任务参数见 [ROS2_CONTRACT.md](ROS2_CONTRACT.md)，测试日志、取证
步骤和报告模板见 [TESTING_LOG_GUIDE.md](TESTING_LOG_GUIDE.md)。跨项目总契约归档
位于 `/home/cat/xbb/MarsDogVisionInteraction/docs/integration/`。

## 3. 下游依赖的关键语义

### 会话 ID

- 唤醒成功后创建 `interaction_id`。
- 从 `EVT_VOICE_CALL_NAME` 到最终 `EVT_STATE_CHANGED(state=idle)` 必须保持同一个 `interaction_id`。
- 每句话使用新的 `utterance_id`；同句话的 KWS、声纹、speech 和最终意图共享该 ID。

### 行为树直接消费的事件

```text
EVT_VOICE_CALL_NAME
EVT_VOICE_COMMAND_SIT / LIE_DOWN / STAND_UP / WAIT / COME / FOLLOW
EVT_VOICE_COMMAND_SHAKE_HAND / HIGH_FIVE / ROLL_OVER / SPIN / RETURN
EVT_VOICE_COMMAND_DROP / PLAY_DEAD / BRING / FETCH / STOP
EVT_STATE_CHANGED
```

`EVT_VOICE_COMMAND_FOLLOW` 必须携带当前 `interaction_id`。行为树收到后把动作系统切到持续 `follow_owner` 模式；该模式一直保持到语音节点发布匹配会话的 `EVT_STATE_CHANGED(state="idle")`。

状态结束原因当前为：

- `interaction_timeout`：最后一次有效语音后默认 10 秒无新语音。
- `stop_listening`：Service 主动结束。

行为树进行唤醒转向、视觉锁定或靠近期间，可调用 VoiceTask 的
`hold_interaction` 暂停空闲终止。请求必须精确携带当前 `interaction_id`、稳定
`hold_token` 和有限 `lease_sec`；同 token 重复调用会续租。到达并准备继续对话
时调用 `release_interaction_hold(reset_idle_timer=true)`，从释放时重新等待 10 秒。
租约不会屏蔽录音、流式 KWS、STOP 或 `stop_listening`，会话终止时全部租约自动
清除。可用 `get_interaction_state` 查询当前 ID 和有效租约。

### KWS 去重

KWS 可在 VAD 结束前发布命令。最终 ASR 意图如果与同一 `utterance_id` 已发布的 KWS 事件相同，不再重复发布；不同命令仍发布。下游仍应按 `interaction_id + utterance_id + event_type` 做幂等保护。

## 4. 启动与验证

```bash
cd /home/cat/xbb/MarsDogVoiceInteraction
source /opt/ros/humble/setup.bash
uv sync --extra dev
uv run marsdog-voice-interaction \
  --ros-args -p config_path:="$PWD/config/voice.yaml"
```

如果需要 `/perception/voice/task`，必须先用 colcon 生成 `.srv`：

```bash
colcon build --base-paths . --packages-select marsdog_voice_interaction
source install/setup.bash
ros2 launch marsdog_voice_interaction voice.launch.py
```

检查：

```bash
ros2 topic info -v /perception/audio_event
ros2 topic echo /perception/audio_event
ros2 service type /perception/voice/task
uv run pytest
```

无硬件下游联调使用 `config/voice.mock.yaml`；无硬件完整节点编排使用
`config/voice.pipeline.mock.yaml`。每次启动用 `runtime_start.providers` 确认实际
Provider，不能只按模式名称判断真机或 Mock。

## 5. 配置责任

| 配置项 | 当前值/含义 |
|---|---|
| `interaction.idle_timeout_sec` | 10 秒，从最后一次有效语音开始计算 |
| `interaction.hold_max_lease_sec` | 单次会话保持租约上限 30 秒，调用方需定期续租 |
| `topics.*` | 对外 Topic/Service 名称 |
| `providers.wakeup` | 讯飞串口唤醒板 `/dev/ttyACM0` |
| `providers.audio` | 16 kHz VAD 和录音 |
| `providers.kws` | 流式关键词命令 |
| `providers.asr` | Paraformer ASR |
| `providers.speaker` | 声纹模型和阈值 |
| `providers.intent_*` | RKLLM 优先、规则回退 |

模型默认放在 `/home/cat/xbb/models`，注册数据放在本项目 `data/`。不得把模型二进制或用户声纹数据复制到其他项目。

## 6. 修改接口时必须回归

- 唤醒事件包含有限数值的原始 `wake_angle`，单位为度，
  `header.frame_id=microphone_array`。Voice 不应用安装 offset/sign；动作项目是
  唯一标定所有者，消费时只允许应用一次安装零偏和方向正负标定。
- `wake_confidence` 始终在 `[0,1]`；硬件原始分数保存在 `wake_score_raw`。
- 同一会话 ID 不在中途变化。
- FOLLOW 事件只发布一次有效指令，且会话结束必有 idle 状态事件。
- `control in {DO,CANCEL}` 时 `should_trigger_behavior_tree=true`。
- Topic 仍为 RELIABLE depth 10，与行为树订阅匹配。
- 新增命令时同时更新 `voice_event_types`、意图映射、`ROS2_CONTRACT.md`，并通知行为树负责人增加白名单与 Behavior 映射。

## 7. 明确不属于本项目的问题

- 人脸框抖动、目标选择：视觉项目。
- 命令优先级、排队和抢占：行为树项目。
- 跟随速度、死区、底盘运动：动作项目。
- 语音节点只保证正确发布会话事件，不能绕过行为树直接控制动作。
