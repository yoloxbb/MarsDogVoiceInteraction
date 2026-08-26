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
intent_source                               str            决策来源：command_lexicon / rule / rkllm / kws / fallback
intent_confidence                           float          意图分类置信度
slots                                       array          槽位 [{"key": "...", "value": "..."}]
response_text                               str            预留：LLM 口语回复
is_executable                               bool           可执行标记（旧字段，新消费者请用 should_trigger_behavior_tree）
should_trigger_behavior_tree                bool           是否交给行为树；确定性指令为 true，旧版意图由 control 决定

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

#### 确定性产品词库与旧版意图指令
| event_type | command_id | 说明 |
|---|---|---|
| `EVT_VOICE_COMMAND_WALK` | `CMD_WALK` | 走/去 |
| `EVT_VOICE_COMMAND_COME` | `CMD_COME_HERE` | 过来/回来/到我这儿 |
| `EVT_VOICE_COMMAND_GO_OUT` | `CMD_GO_OUT` | 出去玩/出去溜溜 |
| `EVT_VOICE_COMMAND_GO_HOME` | `CMD_GO_HOME` | 回家 |
| `EVT_VOICE_COMMAND_APPROACH` | `CMD_APPROACH` | 靠近点 |
| `EVT_VOICE_COMMAND_BACK_UP` | `CMD_BACK_UP` | 退后 |
| `EVT_VOICE_COMMAND_SHAKE_HAND` | `CMD_HAND` | 握手 |
| `EVT_VOICE_COMMAND_HIGH_FIVE` | `CMD_FIVE` | 击掌 |
| `EVT_VOICE_COMMAND_SIT` | `CMD_SIT` | 坐下 |
| `EVT_VOICE_COMMAND_LIE_DOWN` | `CMD_LIE_DOWN` | 趴下 |
| `EVT_VOICE_COMMAND_STAND_UP` | `CMD_STAND_UP` | 站起来 |
| `EVT_VOICE_COMMAND_STAND_STILL` | `CMD_STAND_STILL` | 站好/站着 |
| `EVT_VOICE_COMMAND_WAIT` | `CMD_WAIT` | 等一下 |
| `EVT_VOICE_COMMAND_FOLLOW` | `CMD_FOLLOW` | 跟着我 |
| `EVT_VOICE_COMMAND_ROLL_OVER` | `CMD_ROLL` | 翻滚 |
| `EVT_VOICE_COMMAND_SPIN` | `CMD_SPIN` | 转圈 |
| `EVT_VOICE_COMMAND_RETURN` | `CMD_BACK` | 旧版意图事件；确定性词库中的“回来”不再使用此事件 |
| `EVT_VOICE_COMMAND_DROP` | `CMD_SPIT` | 吐掉 |
| `EVT_VOICE_COMMAND_PLAY_DEAD` | `CMD_DEAD` | 装死 |
| `EVT_VOICE_COMMAND_BRING` | `CMD_BRING_OBJECT` | 把东西拿来 |
| `EVT_VOICE_COMMAND_FETCH` | `CMD_FETCH_OBJECT` | 去找东西 |
| `EVT_VOICE_COMMAND_STOP` | `CMD_STOP` | 停止 |
| `EVT_VOICE_COMMAND_HOLD_POSITION` | `CMD_HOLD_POSITION` | 别动/等着/停/不许动；普通保持位置，不是全局急停 |
| `EVT_VOICE_COMMAND_QUIET` | `CMD_QUIET` | 安静/闭嘴/别叫 |

上表保留常用核心事件和旧版兼容事件，不重复列出完整产品词库。对产品表中
已明确 `ACT_*` 的行，确定性事件名按 `EVT_VOICE_COMMAND_<ACT 后缀>` 生成，
例如 `ACT_EAT_MEAL → EVT_VOICE_COMMAND_EAT_MEAL`。无独立 `ACT_*` 的同类表达可归并到
`EVT_VOICE_CALL_NAME/PRAISE/SCOLD` 或 `EVT_VOICE_COMMAND_TOILET/CLEAN/SLEEP/PLAY`。
完整的短语、事件和动作名对应以 `config/command_catalog.yaml` 为权威源。

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

### 完整确定性产品词库

ASR 文本首先使用 `config/command_catalog.yaml` 做规范化后的完整短语精确匹配。
当前目录覆盖产品表 116 条源数据（不含表头），归并为 81 个路由组和 155 条
可运行中文短语；19 组核心训练指令是这个完整目录的子集。产品表中 138 条英文
参考短语只作元数据，当前不进入直接匹配，避免跨分类重复表达产生错误事件。
匹配成功时：

- 发布目录中的具体 `EVT_VOICE_*`，`intent_source=command_lexicon`；
- 可执行指令为 `control=DO`、`should_trigger_behavior_tree=true`；呼名事件仍进入
  行为树。`PRAISE/SCOLD` 是非执行社交事件，分别使用
  `PRAISE|NONE|NONE` 和 `REPRIMAND|NONE|NONE`，`should_trigger_behavior_tree=false`，
  由下游情绪链路处理；
- 所有目录事件仍经 `/perception/audio_event` 下发，Voice 不直接调用动作系统；
- `slots` 包含 `command_key/matched_phrase/catalog_phrase/command_catalog_version`，并在配置有值时
  带上 `action_name/behavior/catalog_source_rows`；
- 同一句跳过规则和 RKLLM，不产生 `stage_complete stage=intent`；
- 若 KWS 已发布相同事件，则最终结果记为 `suppressed_duplicate`；若 KWS 事件不同，
  为避免同一句执行两个动作，词库结果记为 `suppressed_conflict` 并且不发布。

词库未命中时才进入下面的旧版意图协议。当前完整路由组、核心子集和所有等价短语以
`command_catalog.yaml` 为唯一权威清单；
[COMMAND_CATALOG_TEST_MATRIX.md](COMMAND_CATALOG_TEST_MATRIX.md) 是供测试使用的逐条
可读快照，两者不一致时以 YAML 为准。

### 意图协议：EMOTION|ACTION|CONTROL

解析后的意图以 `EMOTION|ACTION|CONTROL` 三字段形式产出，并随事件发布。

**EMOTION**：NONE / CALM / JOY / EXCITEMENT / ANXIETY / FEAR / SADNESS / LONELINESS / CURIOSITY / PRAISE / REPRIMAND

**ACTION**：NONE / COME / SHAKE_HAND / HIGH_FIVE / SIT / LIE_DOWN / STAND_UP / WAIT / FOLLOW / ROLL_OVER / SPIN / RETURN / DROP / PLAY_DEAD / BRING / FETCH / STOP / UNKNOWN / MULTI

**CONTROL**：NONE / DO / CANCEL / CLARIFY

旧版意图结果中，当 `control ∈ {DO, CANCEL}` 时
`should_trigger_behavior_tree = true`。确定性指令由词库事件直接设置该字段，不依赖
模型 ACTION 标签。

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
│   │  ③ command_lexicon 精确匹配                        ││
│   │     ├─ 命中 → EVT_VOICE_COMMAND_*，跳过意图模型    ││
│   │     └─ 未命中 → 规则/RKLLM → 意图或情绪事件       ││
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

一句话结束后照常发布声纹和 `speech`，并执行 ASR → 确定性指令词库。词库未命中时
才执行意图（RKLLM / 规则）。
同一 `utterance_id` 内：

- 若最终词库/意图的 `event_type` 已被 KWS 发布，则**不重复发布**最终命令事件
- 若词库命中事件与 KWS 不同，抑制词库事件并打印 `command_conflict`
- 词库未命中时，旧版最终意图仍按当前兼容逻辑处理
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
| | `required_shots` | 需要采集的次数（默认 3，范围 1～5） |
| 响应 `result_json` | `ok` | 是否成功 |
| | `step` | 起始步骤 = 1 |
| | `total_steps` | 总共需要采集的次数 |
| | `text` | 第一句朗读提示 |

调用成功后自动开始麦克风采集，注册进度通过 `/perception/voice/enrollment_event` 发布。

#### `cancel_speaker_enrollment` — 取消注册

无特殊参数，返回 `{"ok": true}`。

#### `upload_speaker` — 上传 WAV 注册声纹（兼容接口）

新客户端应使用下文 FastAPI multipart 接口。此任务保留给已有 ROS2 调用方，内部
与 FastAPI 共用同一套 VAD、姓名规范化和落盘逻辑。

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

## 声纹上传 FastAPI

节点按 `speaker_api` 配置启动独立 HTTP 服务。正式配置默认地址为
`http://127.0.0.1:8091`，OpenAPI 页面为 `/docs`。
Swagger 页面只支持选择文件上传，不包含麦克风录音控件。

### `POST /api/v1/speakers`

请求类型：`multipart/form-data`。

| 位置 | 字段 | 必填 | 说明 |
|---|---|---:|---|
| Form | `name` | 是 | 1～128 字符；服务端规范化后最长 64 字符 |
| File | `audio` | 是 | `.wav`；未压缩 16-bit PCM，1～8 声道，8～96 kHz |

成功响应为 HTTP `201`：

```json
{
  "request_id": "c52f7d5f45b84316a5ec6e1898dcf09a",
  "ok": true,
  "name": "张三",
  "shots": 1,
  "audio_path": "/path/to/data/speakers/张三/001.wav",
  "embedding_path": "/path/to/data/speakers/张三/001.npy",
  "source_duration_ms": 3200.0,
  "speech_duration_ms": 1810.0,
  "segment_count": 1,
  "max_speakers": 5,
  "max_samples_per_speaker": 5
}
```

处理顺序固定为：解析 WAV → 转 16 kHz 单声道 → Silero VAD 截取有效语音 →
规范化姓名 → 保存 WAV 和 embedding → 更新 `centroid.npy` 与注册表。相同姓名不会
覆盖旧样本，而是使用 `001/002/...` 递增。

落盘根目录只读取节点启动配置中的 `storage.root`。任何 HTTP 接口都不定义或接受
可生效的路径参数，客户端不能覆盖该目录。上传成功还会明确返回
`audio_valid=true` 和 `has_effective_speech=true`；格式错误、文件截断、VAD 没有
检测到语音或有效语音不足 0.5 秒时不会创建目录或记录。

系统硬限制最多 5 个不同人员，人数按注册表和 `data/speakers` 实际人员目录的并集
计算。同名追加样本不增加人数；达到上限时新增人员返回 HTTP `409`。如果历史数据
已经超过 5 人，系统不会自动删除生物特征数据，但会拒绝继续新增，直到主动删除到
限制以内。

单个人员最多保存 5 个编号样本。上传同名人员时，当前样本数达到 5 后直接返回
HTTP `409`，不会继续执行 VAD/声纹推理，也不会生成 `006.wav` 或 `006.npy`。
ROS2 录制注册的 `required_shots` 同样只能取 1～5。

| 状态码 | 含义 |
|---:|---|
| `201` | 注册并落盘成功 |
| `400` | 空文件 |
| `404` | 修改或删除的人员不存在 |
| `409` | 已有 5 人、单人已有 5 个样本，或修改后的目标姓名已存在 |
| `413` | 超过 `speaker_api.max_upload_mb` |
| `415` | 文件扩展名不是 `.wav` |
| `422` | WAV 格式错误、VAD 无有效语音、有效语音过短或无法提取声纹 |
| `503` | VAD/声纹模型未配置或不可用 |

当前配置使用 `host: 0.0.0.0`，`GET /health` 和全部声纹管理接口都不包含身份验证。
认证模块已移除，后续生产认证方案不属于当前接口契约。声纹文件始终只保存在设备
本地的 `storage.root`。

### `GET /api/v1/speakers`

返回当前人员、样本数、是否存在可用 `centroid.npy`，以及固定上限：

```json
{
  "ok": true,
  "count": 1,
  "max_speakers": 5,
  "max_samples_per_speaker": 5,
  "speakers": [
    {"name": "张三", "shots": 2, "enrolled_at": 1787558400.0, "ready": true}
  ]
}
```

### `PATCH /api/v1/speakers/{name}`

仅修改姓名和对应目录名称，请求为 JSON：

```json
{"name": "主人"}
```

请求模型禁止额外字段，因此不能借此传入或修改存储路径。目标姓名已经存在时返回
HTTP `409`。需要更新声纹音频时，使用 `POST /api/v1/speakers` 上传相同姓名的新
WAV，系统会追加样本并重算 centroid。

### `DELETE /api/v1/speakers/{name}`

删除该人员目录中的 WAV、embedding、centroid 以及注册表记录，同时从当前进程的
声纹检索索引移除。人员不存在时返回 HTTP `404`。此操作不可通过接口指定其他目录。

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
