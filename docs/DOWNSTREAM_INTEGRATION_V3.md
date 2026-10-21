# 语音模块事件与数据结构（AudioEvent v3）

本文定义语音模块发布的事件名称、字段格式、参数取值和消息示例。

## 1. 发布接口

| 项目 | 定义 |
|---|---|
| Topic | `/perception/audio_event` |
| ROS2 消息类型 | `std_msgs/msg/String` |
| 消息内容 | `msg.data` 为 UTF-8 JSON 字符串 |
| 协议版本 | `schema_version=3` |
| QoS | `RELIABLE / KEEP_LAST / depth=10` |

## 2. 公共数据结构

### 2.1 完整消息示例

以“去厨房拿狗绳”为例。所有消息均包含以下 12 个顶层字段，示例中的 ID 和时间为示意值。

```json
{
  "schema_version": 3,
  "header": {"stamp": 1792454400.125, "frame_id": "microphone_array"},
  "event_type": "EVT_VOICE_COMMAND_FETCH_FROM_LOCATION",
  "interaction_id": "session-demo",
  "utterance_id": "utterance-fetch",
  "wake_id": "wake-demo",
  "asr_text": "去厨房拿狗绳",
  "source": "parameterized_command",
  "speaker": {"id": "owner", "role": "owner", "status": "matched"},
  "direction": null,
  "params": {"location_id": "kitchen", "object_name": "dog leash"},
  "dispatch": "command"
}
```

### 2.2 顶层字段

| 字段 | 类型 | 定义 |
|---|---|---|
| `schema_version` | integer | 协议版本，固定为 `3` |
| `header` | object | 发布时间和方向坐标系，见第 2.3 节 |
| `event_type` | string | 事件名称，见第 3 节 |
| `interaction_id` | string | 语音会话 ID；无会话关联时为空字符串 |
| `utterance_id` | string | 一句话语或呼唤通知的 ID；同句话语的身份、转写和语义事件共用；非话语事件为空字符串 |
| `wake_id` | string | 一次硬件唤醒的 ID；唤醒及其身份结果共用；普通话语可关联本次唤醒；呼唤与独立声源为空字符串 |
| `asr_text` | string | 转写文本；即时关键词、固件通知和非语音事件可为空字符串 |
| `source` | string | 事件产生来源，见第 2.7 节 |
| `speaker` | object 或 null | 本次事件关联的说话者身份；无相关身份时为 `null` |
| `direction` | object 或 null | 本次事件关联的方向快照；未提供方向时为 `null` |
| `params` | object | 事件参数；无参数时为 `{}` |
| `dispatch` | string | 事件分类：`command / reaction / notify / reject` |

字段名区分大小写。所有数值均为有限数值；布尔值使用 JSON `true/false`。
空字符串、`null` 和空对象分别表示无关联字符串、无相关对象和无参数。

### 2.3 header

| 字段 | 类型 | 定义 |
|---|---|---|
| `stamp` | number | 消息发布时间，Unix 秒数，包含小数部分 |
| `frame_id` | string | 方向坐标系，固定为 `microphone_array` |

### 2.4 speaker

非空时包含以下三个字段：

| 字段 | 类型 | 定义 |
|---|---|---|
| `id` | string | `owner`、`family_member_1`～`family_member_4` 或 `unknown` |
| `role` | string | `owner / family / stranger / undetermined` |
| `status` | string | 声纹核验状态，见下表 |

| status | 含义 |
|---|---|
| `matched` | 本次音频匹配到已登记身份 |
| `pending` | 本次音频的身份核验尚未完成 |
| `no_match` | 有效语音明确低于匹配阈值，未匹配身份 |
| `ambiguous` | 候选身份分差不足，身份不确定 |
| `insufficient_audio` | 可用语音不足 |
| `unavailable` | 无可用核验结果，如缺少模板或模型不可用 |
| `not_checked` | 本次事件未做声纹核验 |

`matched` 时，主人为 `owner/owner`，家人为 `family_member_N/family`。
`no_match` 时为 `unknown/stranger`；`pending/ambiguous/insufficient_audio/unavailable/not_checked`
时为 `unknown/undetermined`。

话语事件中的身份来自该句音频，与唤醒者身份独立。即时关键词事件可先发送
`unknown/undetermined/pending`，之后的身份事件通过相同 `interaction_id/utterance_id` 关联。
固件呼唤未核验时为 `unknown/undetermined/not_checked`；独立声源和会话结束事件为 `null`。

### 2.5 direction

非空时包含以下三个字段，坐标系由 `header.frame_id` 表示：

| 字段 | 类型 | 定义 |
|---|---|---|
| `angle_deg` | number | 原始方向角，单位度；有效范围 `0 <= angle_deg < 360`；无效时为 `-1` |
| `valid` | boolean | 发布时方向是否有效；`0°` 是有效角度 |
| `age_ms` | number | 从方向测量到消息发布的总年龄，单位毫秒；未知为 `-1` |

有效方向的 `age_ms` 有限且非负。方向无效时可保留已知年龄；没有方向快照时整个对象为 `null`。
唤醒方向对应本次唤醒，呼唤方向对应本句话语或通知的采集窗口，独立声源方向对应其触发更新。
方向角是麦克风阵列原始角度。

### 2.6 dispatch

| 值 | 含义 |
|---|---|
| `command` | 已确认的具体业务指令 |
| `reaction` | 夸赞、责备或通用声源反应事件 |
| `notify` | 唤醒、身份、转写、呼唤或状态通知 |
| `reject` | 未支持目标、未知命令或无有效结果；原因由 `params.reason` 表示 |

同一参数事件可因目标是否受支持而使用不同分类，例如地点有效时为 `command`，未知地点为 `reject`。

### 2.7 source

| 来源值 | 含义 |
|---|---|
| `kws` | 流式关键词识别 |
| `command_lexicon` | 固定词句识别 |
| `parameterized_command` | 带物品、地点或人物参数的句式识别 |
| `respeaker_keyword` | ReSpeaker 固件关键词 |
| `respeaker_sherpa` | ReSpeaker 主机关键词识别 |
| `respeaker_serial` | ReSpeaker 串口唤醒 |
| `respeaker_vad` | ReSpeaker 声音活动更新 |
| `speaker` | 声纹识别结果 |
| `asr` | 语音转写 |
| `interaction` | 语音会话状态 |

## 3. 事件清单

### 3.1 唤醒、身份、转写与状态事件

| event_type | dispatch | 事件含义与数据 |
|---|---|---|
| `EVT_VOICE_WAKEUP` | `notify` | 硬件唤醒；interaction_id/wake_id 非空，params.wake_word 为唤醒词，speaker 可为 pending |
| `EVT_VOICE_WAKE_SPEAKER_RESULT` | `notify` | 唤醒声纹结果；与对应 WAKEUP 共用 interaction_id/wake_id |
| `EVT_VOICE_MASTER_ID` | `notify` | 本句识别为主人；speaker.id=owner |
| `EVT_VOICE_FOLK_ID` | `notify` | 本句识别为家人；speaker.id=family_member_1～family_member_4 |
| `EVT_VOICE_UNMASTER_ID` | `notify` | 本句身份未确认；具体核验状态由 speaker.status 表示 |
| `speech` | `notify` | 本句转写结果，文本位于 asr_text |
| `EVT_VOICE_CALL` | `notify` | 呼唤；params.call_kind=here/name，interaction_id/utterance_id 非空，wake_id 为空 |
| `EVT_STATE_CHANGED` | `notify` | 语音会话结束；保留结束会话的 interaction_id，params.state=idle，params.reason 为结束原因 |
| `EVT_VOICE_SOUND_SOURCE_DETECTED` | `reaction` | 独立声音活动；interaction_id/utterance_id/wake_id 为空，speaker=null，params.sound_id 为事件唯一 ID |

呼唤子类 `here` 对应“这儿／这里这里／到这来”，`name` 对应昵称呼唤。
硬件唤醒使用 WAKEUP，话语中的昵称呼唤使用 CALL。呼唤携带已有会话的 interaction_id。
方向无效的唤醒和呼唤仍可包含通知数据，direction.valid=false；声源事件携带有效方向。
声源事件描述声音活动与方向，不携带拍手、嘬声或异响的声音类别标签。

### 3.2 固定词句事件：43 项

下表与 [语音识别内容.xlsx](语音识别内容.xlsx) 的固定路由对应，示例列为常用识别表达。

<!-- workbook-fixed-events:start -->
| Excel 行 | 识别示例 | event_type | dispatch |
|---|---|---|---|
| 3 | 小火小火、小狗、跟屁虫、嘬嘬嘬 | `EVT_VOICE_CALL` | `notify` |
| 4 | 安静、别叫 | `EVT_VOICE_COMMAND_QUIET` | `command` |
| 5 | 别动、停、不可以 | `EVT_VOICE_COMMAND_HOLD_POSITION` | `command` |
| 6 | 走、跟着我、跟上 | `EVT_VOICE_COMMAND_FOLLOW` | `command` |
| 7 | 过来、回来 | `EVT_VOICE_COMMAND_COME` | `command` |
| 8 | 再过来点、靠近点 | `EVT_VOICE_COMMAND_APPROACH` | `command` |
| 9 | 前进、往前走走 | `EVT_VOICE_COMMAND_MOVE_FORWARD` | `command` |
| 10 | 后退、往后稍稍 | `EVT_VOICE_COMMAND_BACK_UP` | `command` |
| 11 | 向左转、左转 | `EVT_VOICE_COMMAND_TURN_LEFT` | `command` |
| 12 | 向右转、右转 | `EVT_VOICE_COMMAND_TURN_RIGHT` | `command` |
| 13 | 走快点 | `EVT_VOICE_COMMAND_SPEED_UP` | `command` |
| 14 | 走慢点 | `EVT_VOICE_COMMAND_SLOW_DOWN` | `command` |
| 15 | 坐、坐下 | `EVT_VOICE_COMMAND_SIT` | `command` |
| 16 | 趴下、躺下 | `EVT_VOICE_COMMAND_LIE_DOWN` | `command` |
| 17 | 站起来、站好、站着 | `EVT_VOICE_COMMAND_STAND_UP` | `command` |
| 18 | 低头、看地板 | `EVT_VOICE_COMMAND_LOOK_DOWN` | `command` |
| 19 | 抬头、往上看 | `EVT_VOICE_COMMAND_LOOK_UP` | `command` |
| 20 | 叫两声、说话 | `EVT_VOICE_COMMAND_BARK` | `command` |
| 21 | 握手、抬手 | `EVT_VOICE_COMMAND_SHAKE_HAND` | `command` |
| 22 | 击掌、拍手 | `EVT_VOICE_COMMAND_HIGH_FIVE` | `command` |
| 23 | 转圈、转一圈 | `EVT_VOICE_COMMAND_SPIN` | `command` |
| 24 | 松口、不可以咬 | `EVT_VOICE_COMMAND_DROP` | `command` |
| 25 | 绕圈、绕一圈 | `EVT_VOICE_COMMAND_CIRCLE_AROUND` | `command` |
| 26 | 装死、倒下来 | `EVT_VOICE_COMMAND_PLAY_DEAD` | `command` |
| 27 | 哈喽、你叫什么名字 | `EVT_VOICE_COMMAND_SOCIAL_GREETING` | `command` |
| 28 | 去睡觉、回窝睡觉 | `EVT_VOICE_COMMAND_SLEEP` | `command` |
| 29 | 想吃啥、喝水吗 | `EVT_VOICE_COMMAND_FOOD_CARE` | `command` |
| 30 | 来玩、一起玩 | `EVT_VOICE_COMMAND_PLAY` | `command` |
| 31 | 不玩了、玩累了 | `EVT_VOICE_COMMAND_PLAY_END` | `command` |
| 32 | 出去玩、出门溜溜 | `EVT_VOICE_COMMAND_GO_OUT` | `command` |
| 33 | 来拔河 | `EVT_VOICE_COMMAND_TUG_OF_WAR` | `command` |
| 34 | 来追我呀、抓小狗游戏 | `EVT_VOICE_COMMAND_CHASE` | `command` |
| 35 | 来捉迷藏 | `EVT_VOICE_COMMAND_HIDE_AND_SEEK` | `command` |
| 36 | 飞盘游戏 | `EVT_VOICE_COMMAND_FETCH_FRISBEE` | `command` |
| 37 | 去追球 | `EVT_VOICE_COMMAND_CHASE_BALL` | `command` |
| 38 | 真棒、小宝贝、乖狗狗 | `EVT_VOICE_COMMAND_PRAISE` | `reaction` |
| 39 | 我生气了、罚你不准吃饭 | `EVT_VOICE_COMMAND_SCOLD` | `reaction` |
| 40 | 不开心、难过 | `EVT_VOICE_COMMAND_OWNER_UNHAPPY` | `command` |
| 41 | 开心、太好了 | `EVT_VOICE_COMMAND_OWNER_HAPPY` | `command` |
| 47 | 我回来啦、回来咯 | `EVT_VOICE_COMMAND_OWNER_RETURNED` | `command` |
| 48 | 我走啦、我回去啦、再见 | `EVT_VOICE_COMMAND_OWNER_GOING_OUT` | `command` |
| 49 | 这是啥、看前边 | `EVT_VOICE_COMMAND_WHAT_IS_THIS` | `command` |
| 50 | 我是谁、看下谁来了 | `EVT_VOICE_COMMAND_WHO_IS_IT` | `command` |
<!-- workbook-fixed-events:end -->

### 3.3 参数、未知结果与声源事件

| Excel 行 | 识别示例 | event_type | dispatch | params 核心字段 |
|---|---|---|---|---|
| 42 | 去拿狗绳、去找玩具、拿狗碗过来 | `EVT_VOICE_COMMAND_FETCH` | `command / reject` | object_name，或 object_name=NONE 加 object_group=toy |
| 43 | 去找主人 | `EVT_VOICE_COMMAND_FIND_PERSON` | `command / reject` | person_id |
| 44 | 到客厅去 | `EVT_VOICE_COMMAND_GO_TO_LOCATION` | `command / reject` | location_id |
| 45 | 去厨房拿狗绳 | `EVT_VOICE_COMMAND_FETCH_FROM_LOCATION` | `command / reject` | location_id，加具体 object_name 或 object_group |
| 46 | 未匹配到明确指令的语句 | `EVT_VOICE_COMMAND_UNKNOWN` | `reject` | reason |
| 51 | 有方向的声音活动 | `EVT_VOICE_SOUND_SOURCE_DETECTED` | `reaction` | sound_id、sound_trigger_stamp |

目标已登记时，四类参数事件为 `command`；目标未支持时为 `reject`，原目标名称保留在 mention 字段。
组合取物事件同时包含地点和物品，两者任一未支持时整条事件为 `reject`。
`speaker.id` 表示发话者，`params.person_id` 表示被寻找者。

## 4. params 字段与取值

### 4.1 事件参数

| 字段 | 类型 | 所属事件 | 定义 |
|---|---|---|---|
| `wake_word` | string | 唤醒、固件呼唤 | 识别到的唤醒词或呼唤词 |
| `call_kind` | string | CALL | `here / name` |
| `object_name` | string | FETCH、FETCH_FROM_LOCATION | 规范物品类别；未指定具体类别时为 `NONE` |
| `object_group` | string | FETCH、FETCH_FROM_LOCATION | 泛称物品组，玩具为 `toy` |
| `object_mention` | string | 物品参数事件 | 句中提取的物品名称，可选 |
| `location_id` | string | GO_TO_LOCATION、FETCH_FROM_LOCATION | 地点编码；未支持时为 `NONE` |
| `location_name` | string | 地点参数事件 | 地点名称，可选 |
| `location_mention` | string | 地点参数事件 | 句中提取的地点名称，可选 |
| `person_id` | string | FIND_PERSON | 人物编码；未登记时为 `NONE` |
| `person_name` | string | FIND_PERSON | 人物名称，可选 |
| `person_mention` | string | FIND_PERSON | 句中提取的人物名称，可选 |
| `reason` | string | reject、STATE_CHANGED | 目标未支持、未知结果或会话结束的原因 |
| `sound_id` | string | SOUND_SOURCE_DETECTED | 独立声音事件的唯一 ID |
| `sound_trigger_stamp` | number | SOUND_SOURCE_DETECTED | 触发声音活动更新的接收时间，Unix 秒数 |
| `state` | string | STATE_CHANGED | 会话结束状态，值为 `idle` |

普通固定指令、身份和转写事件的 params 为 `{}`。CALL 包含 call_kind；
固件 CALL 可另带 wake_word。组合取物事件同时携带地点和物品参数。

### 4.2 原因值

| reason | 含义 |
|---|---|
| `unsupported_object` | 物品未支持 |
| `unsupported_location` | 地点未支持 |
| `unsupported_object_and_location` | 物品和地点均未支持 |
| `unregistered_person` | 人物未登记 |
| `unrecognized_command` | 未识别到支持的指令 |
| `interaction_timeout` | 语音会话超时结束 |
| `stop_listening` | 语音会话主动停止 |

### 4.3 地点与人物编码

| location_id | 名称 |
|---|---|
| `dog_bed` | 狗窝 |
| `room` | 房间 |
| `bedroom` | 卧室 |
| `living_room` | 客厅 |
| `kitchen` | 厨房 |
| `entrance` | 门口 |

| person_id | 名称 |
|---|---|
| `owner` | 主人 |

地点与人物编码词表分别见 [location_targets.yaml](../config/location_targets.yaml) 和
[person_targets.yaml](../config/person_targets.yaml)。

### 4.4 取物目标参数

[语音识别内容.xlsx](语音识别内容.xlsx) 第 42、45 行明确列出
“狗绳／狗绳子、玩具、狗碗等物品”。本节列出这三项的参数格式。

| 识别目标 | object_name | object_group | Excel 行 |
|---|---|---|---|
| 狗绳、狗绳子 | `dog leash` | 不提供 | 42、45 |
| 狗碗 | `dog bowl` | 不提供 | 42、45 |
| 玩具 | `NONE` | `toy` | 42、45 |

“玩具”是泛称，不指定具体物品类别，使用 `object_name=NONE, object_group=toy`。
未知物品使用 `object_name=NONE`，同时提供 object_mention/reason，dispatch 为 reject。

第 36 行“飞盘游戏”和第 37 行“去追球”分别使用固定事件
`EVT_VOICE_COMMAND_FETCH_FRISBEE`、`EVT_VOICE_COMMAND_CHASE_BALL`，params 均为 `{}`。

## 5. 消息示例

每个示例都包含完整的 12 个顶层字段。

### 5.1 即时关键词事件

```json
{
  "schema_version": 3,
  "header": {"stamp": 1792454401.0, "frame_id": "microphone_array"},
  "event_type": "EVT_VOICE_COMMAND_COME",
  "interaction_id": "session-demo",
  "utterance_id": "utterance-come",
  "wake_id": "wake-demo",
  "asr_text": "",
  "source": "kws",
  "speaker": {"id": "unknown", "role": "undetermined", "status": "pending"},
  "direction": null,
  "params": {},
  "dispatch": "command"
}
```

### 5.2 同句话语的声纹结果

```json
{
  "schema_version": 3,
  "header": {"stamp": 1792454401.5, "frame_id": "microphone_array"},
  "event_type": "EVT_VOICE_MASTER_ID",
  "interaction_id": "session-demo",
  "utterance_id": "utterance-come",
  "wake_id": "wake-demo",
  "asr_text": "",
  "source": "speaker",
  "speaker": {"id": "owner", "role": "owner", "status": "matched"},
  "direction": null,
  "params": {},
  "dispatch": "notify"
}
```

此消息与第 5.1 节的关键词事件共用 interaction_id/utterance_id。

### 5.3 “这儿”呼唤

```json
{
  "schema_version": 3,
  "header": {"stamp": 1792454402.0, "frame_id": "microphone_array"},
  "event_type": "EVT_VOICE_CALL",
  "interaction_id": "session-demo",
  "utterance_id": "utterance-here",
  "wake_id": "",
  "asr_text": "",
  "source": "respeaker_keyword",
  "speaker": {"id": "unknown", "role": "undetermined", "status": "not_checked"},
  "direction": {"angle_deg": 90.0, "valid": true, "age_ms": 120.0},
  "params": {"call_kind": "here", "wake_word": "这儿"},
  "dispatch": "notify"
}
```

### 5.4 未支持的地点

```json
{
  "schema_version": 3,
  "header": {"stamp": 1792454403.0, "frame_id": "microphone_array"},
  "event_type": "EVT_VOICE_COMMAND_GO_TO_LOCATION",
  "interaction_id": "session-demo",
  "utterance_id": "utterance-unsupported",
  "wake_id": "wake-demo",
  "asr_text": "去办公室",
  "source": "parameterized_command",
  "speaker": {"id": "owner", "role": "owner", "status": "matched"},
  "direction": null,
  "params": {"location_id": "NONE", "location_mention": "办公室", "reason": "unsupported_location"},
  "dispatch": "reject"
}
```

### 5.5 独立声源

```json
{
  "schema_version": 3,
  "header": {"stamp": 1792454404.0, "frame_id": "microphone_array"},
  "event_type": "EVT_VOICE_SOUND_SOURCE_DETECTED",
  "interaction_id": "",
  "utterance_id": "",
  "wake_id": "",
  "asr_text": "",
  "source": "respeaker_vad",
  "speaker": null,
  "direction": {"angle_deg": 45.0, "valid": true, "age_ms": 80.0},
  "params": {"sound_id": "sound-demo", "sound_trigger_stamp": 1792454403.96},
  "dispatch": "reaction"
}
```

## 6. 事件关联

| 关联对象 | 关联字段 | 数据关系 |
|---|---|---|
| 硬件唤醒与唤醒声纹结果 | interaction_id、wake_id | 同次唤醒使用相同 ID；会话内再次唤醒时 wake_id 更新 |
| 一句话的关键词、声纹、转写和语义结果 | interaction_id、utterance_id | 同句话语使用相同 ID，不同话语使用不同 utterance_id |
| 呼唤通知 | interaction_id、utterance_id | 属于已有语音会话，wake_id 为空 |
| 独立声源 | params.sound_id | 与语音会话独立，三个公共关联 ID 均为空 |
| 会话结束 | interaction_id | 标识本次结束的语音会话，结束状态及原因位于 params |

即时关键词事件的 asr_text 可为空，speaker.status 可为 pending；本句的身份和转写随后分别发布。
ASR 产生的具体语义事件可直接携带本句转写和身份。上述事件的 event_type 各不相同。
