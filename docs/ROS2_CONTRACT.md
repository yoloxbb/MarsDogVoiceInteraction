# 语音 ROS2 契约

## 概览

| Topic / Service | 类型 | QoS |
|---|---|---|
| `/perception/audio_event` | `std_msgs/String` (UTF-8 JSON) | RELIABLE, KEEP_LAST, depth 10 |
| `/perception/voice/enrollment_event` | `std_msgs/String` (UTF-8 JSON) | RELIABLE, KEEP_LAST, depth 10 |
| `/perception/voice/task` | `marsdog_voice_interaction/srv/VoiceTask` | Service |

---

## `/perception/audio_event`

语音交互核心输出。事件驱动发布，`schema_version: 1`。

### 完整字段

```text
schema_version                              int            固定为 1
header.stamp                                float          事件时间戳（Unix epoch）
header.frame_id                             str            原始阵列角坐标系，固定 "microphone_array"

event_type                                  str            事件类型（见下方枚举）
interaction_id                              str            一次唤醒到会话结束共享的会话 ID
utterance_id                                str            单次语音的唯一 ID（同一句话的所有事件共享）

wake_word                                   str            唤醒词原文（如 "xiao3 wei1 xiao3 wei1"）
wake_angle                                  float          XFYun 原始唤醒源方向角（度），未做安装标定
wake_confidence                             float          归一化唤醒置信度，范围 [0,1]
wake_score_raw                              float          唤醒硬件原始分数，仅诊断使用

asr_text                                    str            ASR 转写文本
language                                    str            语言标签：zh / en / ja / ko / yue

speaker_id                                  str            说话人 ID，"unknown" 表示陌生人
speaker_confidence                          float          声纹匹配置信度

emotion                                     str            情绪标签（见意图协议）
action                                      str            动作标签（见意图协议）
control                                     str            控制标签（见意图协议）

command_id                                  str            指令 ID（如 CMD_SIT、CMD_COME_HERE）
intent_category                             str            意图类别：command / cancel / clarify / praise / blame / emotion / none
intent_source                               str            意图来源：rule / rkllm / kws / fallback
intent_confidence                           float          意图分类置信度
slots                                       array          槽位 [{"key": "...", "value": "..."}]
response_text                               str            预留：LLM 口语回复
is_executable                               bool           可执行标记（旧字段，新消费者请用 should_trigger_behavior_tree）
should_trigger_behavior_tree                bool           是否触发行为树（control ∈ {DO, CANCEL}）

danger_type                                 str            危险检测类型（预留）
danger_angle                                float          危险检测角度（预留）

state                                       str            当前状态机状态
previous_state                              str            上一状态机状态
state_reason                                str            状态变更原因（如 interaction_timeout、stop_listening）

latency_ms                                  float          处理延迟（ms）
```

`wake_angle` 属于 `microphone_array` 坐标系。Voice 只透传硬件原始角度，不应用
安装零偏或方向正负标定；动作项目是唯一标定所有者，并且只能在执行转向时应用
一次 offset/sign 标定。其他事件继续携带同一完整 header，未使用声源角时忽略
`wake_angle` 即可。

### event_type 枚举

#### 唤醒
| event_type | 说明 |
|---|---|
| `EVT_VOICE_CALL_NAME` | 唤醒词命中，携带 `wake_word` / `wake_angle` / `wake_confidence` |

#### 声纹识别
| event_type | 说明 |
|---|---|
| `EVT_VOICE_MASTER_ID` | 识别为主人（已知 speaker） |
| `EVT_VOICE_STRANGER_ID` | 识别为陌生人 |

#### ASR 转写
| event_type | 说明 |
|---|---|
| `speech` | ASR 转写结果，携带 `asr_text` / `language`，此时 intent 尚未就绪 |

#### 意图分类 — 指令
| event_type | command_id | 说明 |
|---|---|---|
| `EVT_VOICE_COMMAND_COME` | `CMD_COME_HERE` | 过来 |
| `EVT_VOICE_COMMAND_SHAKE_HAND` | `CMD_HAND` | 握手 |
| `EVT_VOICE_COMMAND_HIGH_FIVE` | `CMD_FIVE` | 击掌 |
| `EVT_VOICE_COMMAND_SIT` | `CMD_SIT` | 坐下 |
| `EVT_VOICE_COMMAND_LIE_DOWN` | `CMD_LIE_DOWN` | 趴下 |
| `EVT_VOICE_COMMAND_STAND_UP` | `CMD_STAND_UP` | 站起来 |
| `EVT_VOICE_COMMAND_WAIT` | `CMD_WAIT` | 等一下 |
| `EVT_VOICE_COMMAND_FOLLOW` | `CMD_FOLLOW` | 跟着我 |
| `EVT_VOICE_COMMAND_ROLL_OVER` | `CMD_ROLL` | 翻滚 |
| `EVT_VOICE_COMMAND_SPIN` | `CMD_SPIN` | 转圈 |
| `EVT_VOICE_COMMAND_RETURN` | `CMD_BACK` | 回来 |
| `EVT_VOICE_COMMAND_DROP` | `CMD_SPIT` | 吐掉 |
| `EVT_VOICE_COMMAND_PLAY_DEAD` | `CMD_DEAD` | 装死 |
| `EVT_VOICE_COMMAND_BRING` | `CMD_BRING_OBJECT` | 把东西拿来 |
| `EVT_VOICE_COMMAND_FETCH` | `CMD_FETCH_OBJECT` | 去找东西 |
| `EVT_VOICE_COMMAND_STOP` | `CMD_STOP` | 停止 |

#### 意图分类 — 情感 / 其他
| event_type | 触发条件 |
|---|---|
| `EVT_VOICE_PRAISE` | emotion=PRAISE |
| `EVT_VOICE_SCOLD` | emotion=REPRIMAND |
| `EVT_VOICE_HAPPY` | emotion=JOY / EXCITEMENT |
| `EVT_VOICE_SAD` | emotion=ANXIETY / FEAR / SADNESS / LONELINESS |
| `EVT_VOICE_NEUTRAL` | 其他非指令意图 |
| `EVT_VOICE_COMMAND_UNKNOWN` | 意图无法识别（control=CLARIFY 或 action=UNKNOWN/MULTI） |

#### 状态变更
| event_type | 说明 |
|---|---|
| `EVT_STATE_CHANGED` | 交互结束，携带 `state="idle"` 和 `state_reason`（见下方） |

### state_reason 取值

| 值 | 说明 |
|---|---|
| `interaction_timeout` | 最后一次有效语音后超过 `idle_timeout_sec` 无新语音 |
| `stop_listening` | 外部通过 `/perception/voice/task` 主动停止 |

### 意图协议：EMOTION|ACTION|CONTROL

解析后的意图以 `EMOTION|ACTION|CONTROL` 三字段形式产出，并随事件发布。

**EMOTION**：NONE / CALM / JOY / EXCITEMENT / ANXIETY / FEAR / SADNESS / LONELINESS / CURIOSITY / PRAISE / REPRIMAND

**ACTION**：NONE / COME / SHAKE_HAND / HIGH_FIVE / SIT / LIE_DOWN / STAND_UP / WAIT / FOLLOW / ROLL_OVER / SPIN / RETURN / DROP / PLAY_DEAD / BRING / FETCH / STOP / UNKNOWN / MULTI

**CONTROL**：NONE / DO / CANCEL / CLARIFY

当 `control ∈ {DO, CANCEL}` 时 `should_trigger_behavior_tree = true`。

### 交互流程与事件序列

```
┌─ IDLE（等待唤醒）──────────────────────────────────────┐
│                          │                             │
│   EVT_VOICE_CALL_NAME    │  唤醒词命中                  │
│   （wake_word / wake_angle / wake_confidence）         │
│                          ▼                             │
│   state: idle → attention                             │
│                          │                             │
│   ┌─ 开始麦克风捕获 ──────────────────────────────────┐│
│   │  同一次唤醒内可持续进行多轮语音                    ││
│   │                                                    ││
│   │  每轮发布的典型事件顺序：                           ││
│   │                                                    ││
│   │  ① EVT_VOICE_MASTER_ID / EVT_VOICE_STRANGER_ID    ││
│   │     ← speaker_id / speaker_confidence              ││
│   │                                                    ││
│   │  ② "speech"                                       ││
│   │     ← asr_text / language / latency_ms             ││
│   │                                                    ││
│   │  ③ EVT_VOICE_COMMAND_* / EVT_VOICE_PRAISE / ...   ││
│   │     ← emotion / action / control / command_id /    ││
│   │       intent_source / should_trigger_behavior_tree ││
│   │                                                    ││
│   │  ①-③ 共享同一个 utterance_id                       ││
│   └────────────────────────────────────────────────────┘│
│                          │                             │
│   EVT_STATE_CHANGED      │  交互结束（超时 / 轮次用尽） │
│   state_reason           │                             │
│   state: → idle                                       │
└───────────────────────────────────────────────────────┘
```

### KWS 流式命令与去重

KWS（关键词识别）在用户说话过程中实时检测指令词，VAD 尚未结束时即可发布
`EVT_VOICE_COMMAND_*`，其 `intent_source` 为 `"kws"`。

一句话结束后照常发布声纹和 `speech`，并执行 ASR → 意图（RKLLM / 规则）。
同一 `utterance_id` 内：

- 若最终意图的 `event_type` 已被 KWS 发布，则**不重复发布**最终命令事件
- 若 `event_type` 不同，最终意图仍会发布
- KWS 不会抑制声纹事件或 `speech` 事件

### 非语音字段说明

本 Topic **不产生** `target_track_id`、`target_identity`、人脸或相机字段。
语音与视觉目标的关联由 `/perception/target_event` 消费者完成。

---

## `/perception/voice/enrollment_event`

声纹注册过程的实时反馈，每采集到一帧有效音频即发布。

### 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `ok` | bool | 是否有活跃的注册会话 |
| `name` | str | 注册人名称 |
| `status` | str | `"captured"` / `"retry"` / `"done"` |
| `step` | int | 当前步骤（1-based） |
| `total_steps` | int | 所需总步数 |
| `shots` | int | 已采集有效次数 |
| `text` | str | 当前步朗读提示文本 |
| `done` | bool | 注册是否完成 |
| `error` | str | 错误信息（失败时，如 "没有进行中的声纹注册会话"） |

### 状态变化示例

```json
// 开始录音 → step=1
{"ok": true, "name": "张三", "status": "captured", "step": 1, "total_steps": 3, "text": "你好小狗，很高兴认识你", "done": false}

// 采集成功 → step=2
{"ok": true, "name": "张三", "status": "captured", "step": 2, "total_steps": 3, "shots": 1, "text": "今天天气不错，我们一起玩吧", "done": false}

// 注册完成
{"ok": true, "name": "张三", "status": "done", "shots": 3, "done": true}
```

---

## `/perception/voice/task`

类型：`marsdog_voice_interaction/srv/VoiceTask`

### Service 定义

```
string task_id         # 请求 ID（原样返回）
string task_type       # 任务类型（见下方）
string params_json     # JSON 参数
---
bool success           # 是否成功
string task_id         # 请求 ID（回显）
string task_type       # 任务类型（回显）
string result_json     # JSON 结果
string error_message   # 错误描述（success=false 时）
float64 latency_ms     # 处理耗时
```

### 支持的 task_type

#### `start_speaker_enrollment` — 开始声纹注册

| 方向 | 字段 | 说明 |
|------|------|------|
| 请求 `params_json` | `name` | 注册人名称 |
| | `required_shots` | 需要采集的次数（默认 3） |
| 响应 `result_json` | `ok` | 是否成功 |
| | `step` | 起始步骤 = 1 |
| | `total_steps` | 总共需要采集的次数 |
| | `text` | 第一句朗读提示 |

调用成功后自动开始麦克风采集，注册进度通过 `/perception/voice/enrollment_event` 发布。

#### `cancel_speaker_enrollment` — 取消注册

无特殊参数，返回 `{"ok": true}`。

#### `upload_speaker` — 上传 WAV 注册声纹

| 方向 | 字段 | 说明 |
|------|------|------|
| 请求 `params_json` | `name` | 注册人名称 |
| | `audio_base64` | WAV 文件 Base64 |
| 响应 `result_json` | `ok` | 是否成功 |

#### `verify_speaker` — 验证说话人

| 方向 | 字段 | 说明 |
|------|------|------|
| 请求 `params_json` | `audio_base64` | WAV 文件 Base64（可选，不传则用最近一次交互音频） |
| 响应 `result_json` | `ok` | 是否成功 |
| | `speaker_id` | 识别结果 |
| | `confidence` | 匹配置信度 |

#### `list_speakers` — 列出已注册说话人

| 方向 | 字段 | 说明 |
|------|------|------|
| 响应 `result_json` | `speakers` | 已注册名称数组 |

#### `delete_speaker` — 删除说话人

| 方向 | 字段 | 说明 |
|------|------|------|
| 请求 `params_json` | `name` | 要删除的名称 |
| 响应 `result_json` | `ok` | 是否成功 |

#### `start_listening` — 手动开始交互

跳过唤醒环节，直接开始录音。用于外部触发（如视觉模块联动）。

可选请求字段 `expected_interaction_id` 用于确认当前活跃会话。若 ID 不匹配，或该
ID 对应会话已经结束，返回失败且不得复活旧会话。

返回：`{"ok": true, "listening": true, "interaction_id": "..."}`

#### `stop_listening` — 手动停止交互

立即结束当前交互会话。

返回：`{"ok": true, "listening": false}`

#### `hold_interaction` — 有限期保持当前会话

请求：

```json
{
  "interaction_id": "当前会话 ID",
  "hold_token": "wake-engagement:<interaction_id>",
  "lease_sec": 6.0,
  "reason": "wake_target_approach"
}
```

- `interaction_id` 必须精确匹配当前活跃会话。
- `hold_token` 必须非空；同 token 重复请求为幂等续租。
- `lease_sec` 必须为有限正数，且不超过
  `interaction.hold_max_lease_sec`（正式配置 30 秒）。
- deadline 使用本地 monotonic clock；租约到期自动删除。
- 有有效租约时只暂停 `interaction_timeout`，不屏蔽录音、KWS、STOP 或
  `stop_listening`。

#### `release_interaction_hold` — 释放保持租约

请求包含 `interaction_id`、`hold_token` 和布尔值 `reset_idle_timer`。只有实际
删除了当前有效 token 时，`reset_idle_timer=true` 才会把最后活动时间重置为
释放时刻。重复释放返回成功但 `released=false`，不会重置计时器。

#### `get_interaction_state` — 查询会话及租约

无参数。返回 `interaction_active/listening`、`interaction_id`、`state`、
`idle_timeout_sec`、`idle_elapsed_sec`、`hold_active` 和 `holds[]`。租约条目包含
`hold_token`、`reason` 和当前 `expires_in_sec`。

---

## Mock 模式

设置 `mock.enabled: true`、`mock.mode: event` 可直接模拟下游语音事件，
不加载硬件和模型。每轮按 `EVT_VOICE_CALL_NAME → 一个同会话语音事件 →
EVT_STATE_CHANGED(state=idle)` 运行，整轮保持同一个 `interaction_id`；空闲终止
仍遵守 10 秒超时和会话保持租约。

```bash
uv run marsdog-voice-interaction \
  --ros-args -p config_path:=config/voice.mock.yaml
```

---

## 状态机

```
IDLE ──(WAKEUP)──▶ ATTENTION ──(SPEECH_START)──▶ INTERACTION
  ▲                                                  │
  │                             (INTENT_PARSED)       │
  │                                  ▼                │
  ├─────────────────────────────────┘                 │
  │              EXECUTION                            │
  │                     │                             │
  └───(TIMEOUT)─────────┘
```

| 状态 | 说明 |
|------|------|
| `idle` | 等待唤醒 |
| `attention` | 唤醒后等待用户说话 |
| `interaction` | 用户正在说话 / 等待意图解析 |
| `execution` | 指令已解析，等待行为树执行 |

---

## 配置与模型

| 组件 | 类型 | 模型路径 |
|------|------|---------|
| 唤醒 | XFYun 串口 | `/dev/ttyACM0` |
| VAD | sherpa-onnx Silero | `vad_model` |
| KWS | sherpa-onnx Zipformer | `model_dir` |
| ASR | sherpa-onnx（支持 SenseVoice / Paraformer） | `asr_model` + `model_type` |
| 声纹 | sherpa-onnx 3D-Speaker | `speaker_model` |
| 意图 | 规则 + RKLLM（Qwen2.5-5B） | `model` (RKLLM) |
