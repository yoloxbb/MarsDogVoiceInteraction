# 语音 ROS2 契约

## 概览

| Topic / Service | 类型 | QoS |
|---|---|---|
| `/perception/audio_event` | `std_msgs/String` (UTF-8 JSON) | RELIABLE, KEEP_LAST, depth 10 |
| `/perception/voice/enrollment_event` | `std_msgs/String` (UTF-8 JSON) | RELIABLE, KEEP_LAST, depth 10 |
| `/perception/voice/task` | `marsdog_voice_interaction/srv/VoiceTask` | Service |

---

## `/perception/audio_event`

语音交互核心输出。事件驱动发布，`schema_version: 2`。

### 完整字段

```text
schema_version                              int            固定为 2
header.stamp                                float          事件时间戳（Unix epoch）
header.frame_id                             str            原始阵列角坐标系，固定 "microphone_array"

event_type                                  str            事件类型（见下方枚举）
interaction_id                              str            一次唤醒到会话结束共享的会话 ID
utterance_id                                str            单次语音的唯一 ID（同一句话的所有事件共享）
wake_id                                     str            每次有效硬件唤醒的唯一 ID；会话内再次唤醒会更新

wake_word                                   str            唤醒词原文（如 "xiao3 wei1 xiao3 wei1"）
wake_angle                                  float          XFYun 原始唤醒源方向角（度），未做安装标定
wake_confidence                             float          归一化唤醒置信度，范围 [0,1]
wake_score_raw                              float          唤醒硬件原始分数，仅诊断使用

asr_text                                    str            ASR 转写文本
language                                    str            语言标签：zh / en / ja / ko / yue

speaker_id                                  str            `owner`、`family_member_1`～`family_member_4`；`unknown` 表示未匹配
speaker_confidence                          float          声纹余弦匹配分数，不是校准后的身份概率
speaker_role                                str            唤醒身份：owner / family / stranger / undetermined
speaker_status                              str            唤醒声纹状态：pending / matched / no_match / ambiguous / insufficient_audio / unavailable
speaker_reason                              str            声纹拒识或不可用原因；诊断用

social                                      str            Model Intent SOCIAL 标签
intent                                      str            Model Intent INTENT 标签
control                                     str            控制标签（见意图协议）
emotion                                     str            兼容字段，当前镜像 social
action                                      str            兼容字段；模型事件镜像 intent，具体指令保留 command_key

command_id                                  str            指令 ID（如 CMD_SIT、CMD_COME_HERE）
intent_category                             str            意图类别：command / cancel / clarify / praise / blame / emotion / none
intent_source                               str            决策来源：command_lexicon / rule_rkllm_compatible / rkllm / kws
intent_confidence                           float          意图分类置信度
nlu_protocol                                str            当前为 rkllm_social_intent_control_v1
raw_nlu_tag                                 str            经严格校验的 SOCIAL|INTENT|CONTROL 原始三元组
specific_event_type                         str            KNOWN 摘要对应的具体指令事件；非摘要可为空
dispatch_role                               str            recognition_summary / specific_command / social_reaction / semantic_classification / diagnostic
slots                                       array          槽位 [{"key": "...", "value": "..."}]
response_text                               str            预留：LLM 口语回复
is_executable                               bool           是否为动作命令；社交反应保持 false
should_trigger_behavior_tree                bool           是否交给行为树；动作命令或显式授权的社交反应可为 true

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
| `EVT_VOICE_WAKEUP` | 硬件或 pipeline Mock 唤醒，携带 `wake_id`、`wake_word` / `wake_angle` / `wake_confidence`；声纹未完成时 `speaker_status=pending`，不表示呼名语义 |
| `EVT_VOICE_WAKE_SPEAKER_RESULT` | 同一 `interaction_id` + `wake_id` 的唤醒声纹最终结果；不执行命令 |

#### 声纹识别
| event_type | 说明 |
|---|---|
| `EVT_VOICE_MASTER_ID` | `speaker_id=owner`，识别为主人 |
| `EVT_VOICE_FOLK_ID` | `speaker_id=family_member_1`～`family_member_4`，识别为家人 |
| `EVT_VOICE_UNMASTER_ID` | 未匹配、`speaker_id=unknown`，或命中历史非固定身份名称 |

`EVT_VOICE_STRANGER_ID` 仅保留为旧代码兼容常量，新运行时不再发布。上述三个身份
事件都通过 `/perception/audio_event` 交给行为树等下游消费，Voice 不直接调用动作
系统。

这三个身份事件仍针对**每一句后续语音**。唤醒身份使用
`EVT_VOICE_WAKE_SPEAKER_RESULT`：有效语音明确低于匹配阈值才是
`speaker_role=stranger`；没有音频、多个语音片段、声纹分差不足或模型不可用均为
`undetermined`，不能当成陌生人。正式配置在待机时使用同一麦克风保留最近 3 秒的
内存音频，串口唤醒消息本身不含 PCM。唤醒事件先于声纹结果发布；下游应以
`wake_id` 关联并忽略过期结果。
Tree 对同一 `interaction_id` 的新 `wake_id` 更新声源和身份关联，但保留现有
会话及正在执行的动作；它只将声纹结果记录到当前会话，不自动选择新的动作。
主人、家人、陌生人的差异化行为需要另行确定具体映射。

#### ASR 转写
| event_type | 说明 |
|---|---|
| `speech` | ASR 转写结果，携带 `asr_text` / `language`，此时 intent 尚未就绪 |

#### 确定性产品词库与兼容指令
| event_type | command_id | 说明 |
|---|---|---|
| `EVT_VOICE_COMMAND_CALL_NAME` | `CMD_CALL_NAME` | 词库昵称调用：小狗/小跟屁虫/小宝贝/乖狗狗/小坏蛋；仅表示社交呼名，固定不可执行，不携带硬件唤醒语义 |
| `EVT_VOICE_COMMAND_PRAISE` | `CMD_PRAISE` | 词库精确命中的夸赞表达；非动作命令，以 `social_reaction` 授权 Tree 一次性反应，不占用 Model Intent 的 `EVT_VOICE_PRAISE` |
| `EVT_VOICE_COMMAND_SCOLD` | `CMD_SCOLD` | 词库精确命中的责备表达；非动作命令，以 `social_reaction` 授权 Tree 一次性反应，不占用 Model Intent 的 `EVT_VOICE_SCOLD` |
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
| `EVT_VOICE_COMMAND_RETURN` | `CMD_BACK` | 兼容事件；确定性词库中的“回来”不再使用此事件 |
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
`EVT_VOICE_COMMAND_CALL_NAME/PRAISE/SCOLD` 或
`EVT_VOICE_COMMAND_TOILET/CLEAN/SLEEP/PLAY`。
完整的短语、事件和动作名对应以 `config/command_catalog.yaml` 为权威源。

硬件唤醒、Model Intent 呼名和词库昵称调用是三个独立事件：硬件或 pipeline Mock
唤醒固定为 `EVT_VOICE_WAKEUP`，没有 `utterance_id`；Model Intent 的 `social=CALL`
沿用 `EVT_VOICE_CALL_NAME`；词库昵称固定为 `EVT_VOICE_COMMAND_CALL_NAME`，携带
当前语句的 `utterance_id`、`command_id=CMD_CALL_NAME`、
`dispatch_role=specific_command` 和词库匹配槽位，但其语义固定为
`CALL|NONE|NONE`、`action=NONE`、`is_executable=false`、
`should_trigger_behavior_tree=false`。它不得作为硬件唤醒或通用动作指令。

词库 `PRAISE/SCOLD` 的三轴分别为 `PRAISE|NONE|NONE` 和
`SCOLD|NONE|NONE`，并固定 `action=NONE`、`is_executable=false`、
`dispatch_role=social_reaction`、`should_trigger_behavior_tree=true`。这表示授权 Tree
创建有界的一次性社交反应，不把社交语义伪装成动作命令。

`EVT_VOICE_COMMAND_KNOWN` 只用于 Model Intent 命令摘要：

- `dispatch_role=semantic_classification`，不可执行；同时命中开发侧动作白名单、ASR
  文本动作证据且未被否定时，会在摘要之前额外发布
  `dispatch_role=specific_command` 的具体事件，只有该具体事件可执行。
- 确定性词库和 KWS 命中的是具体特殊事件，不发布此摘要。

#### Model Intent 意图分类业务事件
| event_type | 触发条件 |
|---|---|
| `EVT_VOICE_CALL_NAME` | `social=CALL` |
| `EVT_VOICE_PRAISE` | `social=PRAISE` |
| `EVT_VOICE_SCOLD` | `social=SCOLD` |
| `EVT_VOICE_COMFORT` | `social=COMFORT` |
| `EVT_VOICE_PLAY_INTERACTION` | `social=PLAYFUL`，或 `intent in {PLAY,TUG,DANCE}` |
| `EVT_VOICE_POSITIVE_EMOTION` | `social=OWNER_POSITIVE` |
| `EVT_VOICE_NEGATIVE_EMOTION` | `social=OWNER_NEGATIVE` |
| `EVT_VOICE_STATUS_CARE` | 任一非空 `intent` 且 `control=QUERY` |
| `EVT_VOICE_COMMAND_KNOWN` | 其余非空 `intent` 且 `control in {DO,STOP}` |
| `EVT_VOICE_NEUTRAL` | `NONE|NONE|NONE`；不可执行 |
| `EVT_VOICE_COMMAND_UNKNOWN` | Model Intent 无有效协议输出且兼容规则也无结果，仅作诊断 |

同一三轴结果可以产生多个事件。`PRAISE|SIT|DO` 命中动作白名单，依次发布
`EVT_VOICE_PRAISE`、可执行的 `EVT_VOICE_COMMAND_SIT`，以及不可执行的
`EVT_VOICE_COMMAND_KNOWN` 摘要。若社交轴和意图轴映射到同一个大类事件则只发布
一次。`NONE|NONE|NONE` 固定发布不可执行的 `EVT_VOICE_NEUTRAL`，不伪造成
UNKNOWN。

Model Intent 继续使用下游已定义的上述业务事件名。经显式白名单确认的姿态、移动、
声音等动作，可与确定性词库/KWS 共用相应 `EVT_VOICE_COMMAND_*` 具体事件，并通过
`intent_source` 区分来源；词库社交/产品事件不得占用 Model Intent 业务分类事件。
动作标签优先发布具体事件而不是只发布 KNOWN。其中 `STAND|DO` 固定映射为
`EVT_VOICE_COMMAND_STAND_UP`；`STAY|DO` 必须结合 ASR 原文区分：站立保持语义
映射 `EVT_VOICE_COMMAND_STAND_STILL`，原地不动语义映射
`EVT_VOICE_COMMAND_HOLD_POSITION`。无法可靠区分时只保留不可执行的
`EVT_VOICE_COMMAND_KNOWN`，禁止猜测具体动作。

Voice 不再发布任何 `EVT_VOICE_INTENT_*`。本次硬件唤醒由旧的
`EVT_VOICE_CALL_NAME` 迁移为 `EVT_VOICE_WAKEUP`，属于事件名破坏性变更；本次未修改
Tree、Vision 或 Action，不能把 Voice 发布成功视为这些下游已经完成迁移。

#### Model Intent 找物/捡取目标物门控

`intent in {FETCH,FIND_TOY}` 时，节点在模型分类后使用 ASR 原文查询
`config/object_targets.yaml`。`object_name` 只能是该目录中的 18 个规范视觉类别；
别名命中后统一转换为规范英文名称。

| 条件 | 事件顺序 | 执行权限 |
|---|---|---|
| `FIND_TOY|QUERY` 且目标命中 | `EVT_VOICE_COMMAND_FETCH` → `EVT_VOICE_STATUS_CARE` | 仅 FETCH 为 true |
| `FIND_TOY|DO` 且目标命中 | `EVT_VOICE_COMMAND_FETCH` → `EVT_VOICE_COMMAND_KNOWN` | 仅 FETCH 为 true |
| `FETCH|DO` 且目标命中 | `EVT_VOICE_COMMAND_FETCH` → `EVT_VOICE_COMMAND_KNOWN` | 仅 FETCH 为 true |
| 目标未命中或目录不可用 | 仅原三轴对应的大类事件 | 全部为 false |

目标命中事件和同组摘要携带 `object_name/object_mention/object_matched_alias/`
`object_match_source/object_catalog_version`。未命中时使用
`object_name=NONE/object_match_source=unsupported`；这只表示目标不受支持，不修改
原始 `raw_nlu_tag`。例如“看看那个布偶娃娃在哪里”仍为
`NONE|FIND_TOY|QUERY`，只发布不可执行的 `EVT_VOICE_STATUS_CARE`。

#### 状态变更
| event_type | 说明 |
|---|---|
| `EVT_STATE_CHANGED` | 交互结束，携带 `state="idle"` 和 `state_reason`（见下方） |

### state_reason 取值

| 值 | 说明 |
|---|---|
| `interaction_timeout` | 空闲或总时长阈值触发；非空 ASR 刷新空闲计时。正式配置空闲超时 20 秒、总时长上限 0（禁用） |
| `stop_listening` | 外部通过 `/perception/voice/task` 主动停止 |

### 完整确定性产品词库

ASR 文本首先使用 `config/command_catalog.yaml` 做规范化后的完整短语精确匹配。
当前目录覆盖产品表 116 条源数据（不含表头），加上新增“自己去玩吧”，共 82 个路由组和 156 条
标准中文词/句；每条标准词/句通过五类受控规则生成 10 条扩展，共 1560 条扩展，
另有 70 条人工登记的变体（用于 ASR 稳定错写与自然口语说法），合计
1786 条运行时精确匹配入口。19 组核心训练指令是这个完整目录的子集。产品表中 138 条英文
参考短语只作元数据，当前不进入直接匹配，避免跨分类重复表达产生错误事件。
新增词条“自己去玩吧”的契约为 `command_key=PLAY_ALONE`、
`command_id=CMD_PLAY_ALONE`、`event_type=specific_event_type=EVT_VOICE_COMMAND_PLAY_ALONE`，
`dispatch_role=specific_command`、`control=DO`、`is_executable=true`、
`should_trigger_behavior_tree=true`；slots 包含 `action_name=ACT_PLAY_ALONE` 和
`behavior=去随机位置自己玩`，不含产品表行号 `catalog_source_rows`。该事件需下游单独映射。

匹配成功时：

- 82 个启用路由组均必须声明非空且唯一的 `command_key/command_id/event_type`；目录
  禁止使用 `EVT_VOICE_WAKEUP`、Model Intent 业务分类事件和已停用的
  `EVT_VOICE_INTENT_*`。缺失、重复或越界时节点启动失败；
- 所有目录条目只发布自身的具体特殊事件，不额外发布
  `EVT_VOICE_COMMAND_KNOWN`；
- 具体事件来源为 `intent_source=command_lexicon`；普通动作使用
  `dispatch_role=specific_command`，PRAISE/SCOLD 使用 `social_reaction`；
- 可执行性以 `should_trigger_behavior_tree=true` 为准；核心指令的 `control` 使用
  新三轴语义（例如 QUIET 为 `BARK|STOP`）。词库呼名使用
  `EVT_VOICE_COMMAND_CALL_NAME`，其语义为 `CALL|NONE|NONE`。词库
  `PRAISE/SCOLD` 使用 `EVT_VOICE_COMMAND_PRAISE/SCOLD`，分别使用
  `PRAISE|NONE|NONE` 和 `SCOLD|NONE|NONE`。三类词库社交事件均为非动作事件，
  `is_executable=false`；CALL_NAME 固定 `should_trigger_behavior_tree=false`，
  PRAISE/SCOLD 固定 `dispatch_role=social_reaction`、
  `should_trigger_behavior_tree=true`；
- 所有目录事件仍经 `/perception/audio_event` 下发，Voice 不直接调用动作系统；
- `slots` 包含 `command_key/matched_phrase/catalog_phrase/command_catalog_version/`
  `match_strategy`，受控扩展另带 `expansion_profile/expansion_rule`，并在配置有值时
  带上 `action_name/behavior/catalog_source_rows`；
- `match_strategy=catalog_exact` 表示标准词/句命中，`rule_expansion` 表示配置生成的
  扩展命中；扩展仍是启动时预生成、运行时整句哈希查找，不做子串或模糊匹配；
- 同一句跳过规则和 RKLLM，不产生 `stage_complete stage=intent`；
- 流式 KWS 只缓存候选，不在 VAD 结束前发布业务事件。ASR 完成后通过
  `recognition_arbitration` 在 KWS 与 ASR 链路中选择唯一结果来源；被选中的命令只
  发布具体特殊事件，不附带 KNOWN 摘要。

词库未命中时才进入下面的 Model Intent 意图协议。当前完整路由组、核心子集和所有等价短语以
`command_catalog.yaml` 为唯一权威清单；
[COMMAND_CATALOG_TEST_MATRIX.md](COMMAND_CATALOG_TEST_MATRIX.md) 是供测试使用的逐条
可读快照，两者不一致时以 YAML 为准。

### Model Intent 意图协议：SOCIAL|INTENT|CONTROL

模型必须只输出 `SOCIAL|INTENT|CONTROL`，推理系统提示词固定为：

```text
Classify the owner's MasDog utterance. Return exactly one label in SOCIAL|INTENT|CONTROL format and nothing else.
```

**SOCIAL（8）**：NONE / CALL / PRAISE / SCOLD / COMFORT / PLAYFUL /
OWNER_POSITIVE / OWNER_NEGATIVE

**INTENT（34）**：NONE / GO / COME / FOLLOW / GO_OUT / GO_HOME / APPROACH /
BACK / SIT / LIE / PLAY_DEAD / STAND / STAY / SHAKE / HIGH_FIVE / SPIN /
ROLL / DROP / BARK / EAT / TOILET / CLEAN / SLEEP / PLAY / TUG / FIND_PERSON /
DANCE / FETCH / FIND_TOY / OWNER_LEAVE / OWNER_RETURN / DOG_STATUS /
DOG_PREFERENCE / DOG_CAPABILITY

**CONTROL（4）**：NONE / DO / STOP / QUERY

组合约束：`intent=NONE` 时只能是 `control=NONE`；三个 `DOG_*` 查询标签只能配
`QUERY`；`OWNER_LEAVE/OWNER_RETURN` 只能配 `DO`。模型输出包含多余文本、字段数
错误、未知枚举或非法组合时均拒绝。Model Intent 的大类事件和
`EVT_VOICE_COMMAND_KNOWN` 摘要均为
`should_trigger_behavior_tree=false`；只有通过动作白名单和文本证据门控的具体命令事件为
`true`。词库具体指令仍由确定性目录直接授权，不依赖模型标签。

### 交互流程与事件序列

```
┌─ IDLE（等待唤醒）──────────────────────────────────────┐
│                          │                             │
│   EVT_VOICE_WAKEUP       │  硬件/Mock 唤醒              │
│   （wake_word / wake_angle / wake_confidence）         │
│                          ▼                             │
│   state: idle → attention                             │
│   会话内再次唤醒：保持 interaction_id，更新 wake_id、idle 计时；│
│   被打断话语的旧录音丢弃，唤醒声纹结果随后异步发布。       │
│                          │                             │
│   ┌─ 开始麦克风捕获 ──────────────────────────────────┐│
│   │  同一次唤醒内可持续进行多轮语音                    ││
│   │                                                    ││
│   │  每轮发布的典型事件顺序：                           ││
│   │                                                    ││
│   │  ① MASTER_ID / FOLK_ID / UNMASTER_ID              ││
│   │     ← speaker_id / speaker_confidence              ││
│   │                                                    ││
│   │  ② "speech"                                       ││
│   │     ← asr_text / language / latency_ms             ││
│   │                                                    ││
│   │  ③ command_lexicon 精确匹配                        ││
│   │     ├─ 核心命中 → 目录指定特殊事件                 ││
│   │     ├─ 其他命中 → 目录指定特殊事件                 ││
│   │     └─ 未命中 → Model Intent/兼容规则 → 大类/白名单动作/摘要││
│   │                                                    ││
│   │  ①-③ 共享同一个 utterance_id                       ││
│   └────────────────────────────────────────────────────┘│
│                          │                             │
│   EVT_STATE_CHANGED      │  交互结束（超时 / 主动停止） │
│   state_reason           │                             │
│   state: → idle                                       │
└───────────────────────────────────────────────────────┘
```

### KWS 流式候选与 ASR 仲裁

KWS（关键词识别）在用户说话过程中实时检测指令词，但命中后只缓存候选并打印
`stage_complete stage=kws result=candidate`，不立即发布业务事件。VAD 结束后仍执行
ASR 和声纹，再通过 `stage_complete stage=recognition_arbitration` 选择唯一识别来源。

默认仲裁规则如下：

- 没有 KWS 候选时选择 ASR 链路；
- 同一句出现多个不同 KWS 候选时选择 ASR 链路；
- 中文规范化文本不超过 2 字、英文不超过 2 个词且只有一个 KWS 候选时，仍要求
  ASR 词库命中同一事件；冲突时选择 ASR，未确认时 KWS 不执行；
- 长文本选择 ASR 链路，即使其中包含 KWS 关键词；
- `priority_command_keys` 还必须命中 `priority_asr_aliases` 中同一命令的精确错写，才
  允许覆盖；当前仅为 `吃罐罐→去滚罐` 和 `去拿→去哪`；
- ASR 为空时不允许单一 KWS 候选独立执行；
- 只有被选来源发布一个具体业务事件；词库/KWS 不附带 KNOWN 摘要；
- 声纹身份事件与 `speech` 是链路证据，不参与业务结果互斥。

配置位于 `providers.kws.config`：`publish_mode=deferred`、
`arbitration_mode=exclusive`、`short_max_chars_zh`、`short_max_words_en`、
`asr_long_text_wins`、`kws_fallback_on_asr_empty` 和
`short_requires_asr_agreement`。当前仅允许 deferred/exclusive，另有窄范围
`priority_command_keys + priority_asr_aliases` 精确错写白名单。
配置为其他值会在启动时失败，避免恢复成先执行后纠错的链路。
长度阈值只作用于已配置 KWS 候选，不会自动把目录中的一字/两字词加入关键词文件；
单字关键词默认不启用，必须经过独立误触发验收后显式配置。

### 非语音字段说明

本 Topic **不产生** `target_track_id`、`target_identity`、人脸或相机字段。
需要视觉目标的下游应通过 Vision 的正式 Topic/Service 获取并完成关联；当前契约不
声明 `/perception/target_event` 存在正式发布者。

---

## `/perception/voice/enrollment_event`

声纹注册过程的实时反馈。每次完整录音/VAD 结果处理后发布一次，而不是按音频帧发布。

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
// 第一次采集成功 → 下一提示 step=2
{"ok": true, "name": "owner", "status": "captured", "step": 2, "total_steps": 3, "shots": 1, "text": "今天天气不错，我们一起玩吧", "done": false}

// 当前录音无法提取有效声纹 → 保持当前步骤重试
{"ok": true, "status": "retry", "step": 2, "total_steps": 3, "done": false}

// 注册完成
{"ok": true, "name": "owner", "status": "done", "shots": 3, "done": true}
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
| 请求 `params_json` | `name` | 固定身份：`owner` 或 `family_member_1`～`family_member_4` |
| | `required_shots` | 需要采集的次数（默认 3，范围 1～5） |
| 响应 `result_json` | `ok` | 是否成功 |
| | `step` | 起始步骤 = 1 |
| | `total_steps` | 总共需要采集的次数 |
| | `text` | 第一句朗读提示 |

调用成功后自动开始麦克风采集，注册进度通过 `/perception/voice/enrollment_event` 发布。

现场注册与 HTTP 上传共用 VAD 校验、身份冲突检查及样本追加逻辑。
对已有身份注册会占用新的样本 ID，保留旧 WAV 和 embedding；已有数量与
`required_shots` 合计不得超过 5。需要替换某条样本时使用 HTTP PUT 接口。
全部采集通过后才写入，失败不会覆盖旧样本。

#### `cancel_speaker_enrollment` — 取消注册

无特殊参数，返回 `{"ok": true}`。

#### `verify_speaker` — 验证说话人

| 方向 | 字段 | 说明 |
|------|------|------|
| 请求 `params_json` | `audio_base64` | WAV 文件 Base64（可选，不传则用最近一次交互音频） |
| 响应 `result_json` | `ok` | 是否成功 |
| | `speaker_id` | 识别结果 |
| | `confidence` | 最高余弦相似度，不是概率；拒识时仍可能高于绝对阈值 |
| | `matched` | 是否接受该身份 |
| | `reason` | `matched`、`below_threshold`、`ambiguous_identity`、`audio_too_short`、`unavailable` 等 |
| | `runner_up_score` | 第二身份分数，无第二身份时为 null |
| | `score_margin` | 第一、第二身份的分差，无第二身份时为 null |

显式提供 `audio_base64` 时，按 HTTP 上传相同规则检查 PCM16 WAV、混合声道、
重采样到 16 kHz 并执行 VAD。无有效语音、格式错误或显式空字符串返回失败，
不会转用上一条音频。仅省略字段时使用最近一次交互音频。

识别先取每个身份所有样本的最高分，再比较不同身份；最高分须达到
`providers.speaker.config.match_threshold`（默认 0.5），且与第二身份的差值
达到 `min_score_margin`（默认 0.05），否则返回 `unknown`。同分始终拒识。
这些是初始参数，须用真实设备录音评估误识和拒识后校准。
正式 `type: sherpa` 加载失败保持不可用，不会自动切换到模拟身份；测试模拟
必须显式设置 `type: mock`。

同一身份新增样本还需通过 `speaker_api.same_identity_similarity_threshold`
（默认 0.5）检查。已有身份以其已有样本为参照，新建身份的批次以第一条为参照；
替换以该身份其余样本为参照，无其他样本时不做此项检查。
不相容返回 `speaker_sample_inconsistent`；跨身份冲突仍返回
`speaker_identity_conflict`。声纹注册的 `speech_duration_ms` 只统计 VAD 检出的
片段，不包含额外前后补音及段间静音，可能小于下载 WAV 的总时长。

旧版 `upload_speaker`、`list_speakers`、`delete_speaker` 任务已移除，调用时返回
`unsupported task_type`。文件和样本管理统一使用下文 FastAPI 样本级接口。

#### `start_listening` — 手动开始交互

会话空闲计时在 ASR 返回非空文本时立即刷新，发生在声纹和语义处理之前。
不要求命中词库或触发行为，NEUTRAL、UNKNOWN、语义拒识文本同样续期；
空字符串、纯空白、ASR 异常以及只有 KWS 候选的情况不刷新。
正式配置 `idle_timeout_sec=20`，最后一次非空 ASR 后静默 20 秒结束会话；
`max_duration_sec=0` 不设绝对上限。
每次通过去重和时效检查的硬件再次唤醒也刷新 idle 计时，不重置会话开始时间、
`interaction_id` 或有效 hold；其 `wake_id` 更新。`wake_debounce_sec=0.8` 过滤
硬件重报，`wake_event_max_age_sec=8` 拒绝过旧唤醒（覆盖同步识别期间的排队）。
后续 ASR 指令仍逐句核验
声纹，不能继承唤醒身份作为执行授权。
可调用 `stop_listening` 主动结束。ASR 活动仍记录为 `last_activity_reason=asr_result`。

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
  `interaction.hold_max_lease_sec`（正式配置 20 秒）。
- deadline 使用本地 monotonic clock；租约到期自动删除。
- 有有效租约时只暂停 idle 计时，不屏蔽录音、KWS、STOP、`stop_listening`，也不能
  突破 `interaction.max_duration_sec` 的绝对会话上限。

#### `release_interaction_hold` — 释放保持租约

请求包含 `interaction_id`、`hold_token` 和布尔值 `reset_idle_timer`。只有实际
删除了当前有效 token 时，`reset_idle_timer=true` 才会把最后活动时间重置为
释放时刻。重复释放返回成功但 `released=false`，不会重置计时器。

#### `get_interaction_state` — 查询会话及租约

无参数。返回 `interaction_active/listening`、`interaction_id`、`state`、
`idle_timeout_sec`、`idle_elapsed_sec`、`max_duration_sec`、`active_elapsed_sec`、
`last_activity_reason`、`hold_active` 和 `holds[]`。时长使用单调时钟计算；租约条目包含
`hold_token`、`reason` 和当前 `expires_in_sec`。

---

## 声纹上传 FastAPI

节点按 `speaker_api` 配置启动独立 HTTP 服务。正式配置本机访问地址为
`http://localhost:8091`，OpenAPI 页面为 `/docs`。`GET /speaker-enrollment`
提供浏览器录音页，与声纹服务一同启动。该页把浏览器录音转成 PCM16 WAV，调用
`POST /api/v1/recordings/vad` 获取语音范围和建议截取起止时间；用户可以调整起止点、
试听后再提交原有样本新增接口。VAD 预览不落盘；提交时重新执行完整的上传校验。
浏览器麦克风权限需要安全上下文，本机用 `localhost`；远程使用时需要 HTTPS 或本地转发。

### `POST /api/v1/recordings/vad`

请求类型为 `multipart/form-data`，字段 `audio` 是未压缩的 16-bit PCM WAV；文件大小
受 `speaker_api.max_upload_mb` 限制，时长受 `upload_max_duration_sec` 限制。
响应示例：

```json
{
  "ok": true,
  "source_sample_rate": 48000,
  "source_duration_ms": 3000.0,
  "speech_duration_ms": 1200.0,
  "min_speech_duration_ms": 500.0,
  "segment_count": 1,
  "speech_ranges_ms": [{"start_ms": 800.0, "end_ms": 2000.0}],
  "suggested_crop_ms": {"start_ms": 500.0, "end_ms": 2200.0},
  "valid_for_enrollment": true
}
```

时间以原始录音起点为零，单位毫秒。`speech_ranges_ms` 不含前后补白；
`suggested_crop_ms` 包含 VAD 配置的前后补白。当未检测到语音时，范围为空、建议为
`null`、`valid_for_enrollment=false`，浏览器要求重新录制。该布尔值仅表示有效语音
时长达到注册门槛，不代表声纹提取和身份冲突校验通过。无效 WAV 返回 422。
Swagger `/docs` 的样本上传接口仍是文件选择控件。

### `POST /api/v1/speakers/{name}/samples`

请求类型：`multipart/form-data`。

| 位置 | 字段 | 必填 | 说明 |
|---|---|---:|---|
| Path | `name` | 是 | 只能选择 `owner`、`family_member_1`、`family_member_2`、`family_member_3`、`family_member_4` |
| File | `audio` | 是 | `.wav`；未压缩 16-bit PCM，1～8 声道，8～96 kHz |

成功响应为 HTTP `201`：

```json
{
  "request_id": "c52f7d5f45b84316a5ec6e1898dcf09a",
  "ok": true,
  "name": "owner",
  "shots": 1,
  "sample_id": 1,
  "sample_key": "001",
  "audio_path": "/path/to/data/speakers/owner/001.wav",
  "embedding_path": "/path/to/data/speakers/owner/001.npy",
  "source_sample_rate": 48000,
  "stored_sample_rate": 16000,
  "stored_channels": 1,
  "stored_sample_width_bits": 16,
  "source_duration_ms": 3200.0,
  "speech_duration_ms": 1810.0,
  "segment_count": 1,
  "max_speakers": 5,
  "max_samples_per_speaker": 5,
  "speaker_role": "owner"
}
```

处理顺序固定为：解析 WAV → 转 16 kHz 单声道 → Silero VAD 截取有效语音 →
提取 embedding → 与其他身份样本做相似度冲突校验 → 保存 WAV 和 embedding →
更新 `centroid.npy` 与注册表。相同身份
不会覆盖旧样本，而是分配 `1～5` 中最小的空闲稳定编号。

成功落盘的音频固定为 16 kHz、单声道、16-bit PCM WAV，不保留源采样率格式；
`source_sample_rate` 表示输入采样率，`stored_sample_rate` 表示实际落盘采样率。

落盘根目录只读取节点启动配置中的 `storage.root`。任何 HTTP 接口都不定义或接受
可生效的路径参数，客户端不能覆盖该目录。上传成功还会明确返回
`audio_valid=true` 和 `has_effective_speech=true`；格式错误、文件截断、VAD 没有
检测到语音或有效语音不足 0.5 秒时不会创建目录或记录。

系统固定提供 5 个身份槽位：1 个 `owner` 和 4 个 `family_member_*`。同一身份追加
样本不增加身份数。旧版自由名称数据不再加载、列出或同步到运行时声纹索引。

单个身份最多保存 5 个编号样本。上传相同身份时，当前样本数达到 5 后直接返回
HTTP `409`，不会继续执行 VAD/声纹推理，也不会生成 `006.wav` 或 `006.npy`。
ROS2 录制注册的 `required_shots` 同样只能取 1～5。

| 状态码 | 含义 |
|---:|---|
| `201` | 注册并落盘成功 |
| `400` | 空文件 |
| `404` | 人员或指定样本不存在 |
| `409` | 单个身份已有 5 个样本、跨身份声纹冲突，或样本文件状态不一致 |
| `413` | 超过 `speaker_api.max_upload_mb` |
| `415` | 文件扩展名不是 `.wav` |
| `422` | 身份不在固定枚举、WAV 格式错误、VAD 无有效语音、有效语音过短或无法提取声纹 |
| `503` | VAD/声纹模型未配置或不可用 |

当前配置使用 `host: 0.0.0.0`，`GET /health` 和全部声纹管理接口都不包含身份验证。
认证模块已移除，后续生产认证方案不属于当前接口契约。声纹文件始终只保存在设备
本地的 `storage.root`。

业务校验失败使用结构化错误：`detail.error` 为提示，`detail.code` 为稳定错误码。
跨身份冲突的 `code=speaker_identity_conflict`，并返回
`conflicting_speaker`、`conflicting_sample_id`、`similarity` 和
`similarity_threshold`。当前阈值由
`speaker_api.cross_identity_similarity_threshold` 配置为 `0.75`；该值需要使用实际
设备、麦克风和注册语料验收后再校准。

### 批量新增样本

`POST /api/v1/speakers/{name}/samples/batch` 使用 `multipart/form-data`，重复提交
字段 `audios`，一次允许 1～`speaker_api.max_batch_files` 个文件（当前最多 5 个）。
系统先完成整批文件的格式、VAD、时长、embedding、容量和跨身份冲突校验，全部通过后
才分配稳定 ID 并落盘；任一文件失败时返回 `failed_file_index`（从 1 开始），整批不
创建样本。成功响应包含 `added_count`、`sample_ids` 和逐文件 `samples[]`。

### `GET /api/v1/speakers`

返回当前身份、样本数、角色、是否存在可用 `centroid.npy`、可选/空闲身份及固定上限：

```json
{
  "ok": true,
  "count": 1,
  "max_speakers": 5,
  "max_samples_per_speaker": 5,
  "allowed_names": [
    "owner", "family_member_1", "family_member_2",
    "family_member_3", "family_member_4"
  ],
  "available_names": [
    "family_member_1", "family_member_2",
    "family_member_3", "family_member_4"
  ],
  "speakers": [
    {
      "name": "owner", "role": "owner",
      "shots": 2, "sample_ids": [1, 2],
      "samples_url": "/api/v1/speakers/owner/samples",
      "enrolled_at": 1787558400.0, "ready": true
    }
  ]
}
```

### 单条声纹样本 CRUD

单条样本接口只接受固定身份 `owner/family_member_1～4` 和 `sample_id=1～5`：

| 方法和路径 | 请求 | 用途 |
|---|---|---|
| `POST /api/v1/speakers/{name}/samples` | multipart `audio=.wav` | 给指定身份新增样本 |
| `POST /api/v1/speakers/{name}/samples/batch` | multipart 重复 `audios=.wav` | 原子新增 1～5 个样本 |
| `GET /api/v1/speakers/{name}/samples` | 无 | 列出稳定样本 ID、文件状态和 WAV 下载地址 |
| `GET /api/v1/speakers/{name}/samples/{sample_id}` | 无 | 查询一条样本的元数据 |
| `GET /api/v1/speakers/{name}/samples/{sample_id}/audio` | 无 | 下载 VAD 后的 PCM16 WAV |
| `PUT /api/v1/speakers/{name}/samples/{sample_id}` | multipart `audio=.wav` | 校验新音频并原位替换 WAV 和 embedding |
| `DELETE /api/v1/speakers/{name}/samples/{sample_id}` | 无 | 只删除指定 WAV/embedding |
| `DELETE /api/v1/speakers/{name}/samples` | 无 | 删除该身份的全部样本并移除身份 |
| `DELETE /api/v1/speakers?confirm=true` | Query 必须显式确认 | 删除全部固定身份及其全部样本 |

样本列表响应示例：

```json
{
  "ok": true,
  "name": "owner",
  "role": "owner",
  "shots": 2,
  "max_samples_per_speaker": 5,
  "sample_ids": [1, 2],
  "samples": [
    {
      "sample_id": 1,
      "sample_key": "001",
      "audio_filename": "001.wav",
      "embedding_filename": "001.npy",
      "audio_url": "/api/v1/speakers/owner/samples/1/audio",
      "audio_available": true,
      "embedding_available": true,
      "ready": true,
      "audio_size_bytes": 32044,
      "updated_at": 1787558400.0
    }
  ]
}
```

`sample_id` 在已有样本生命周期内保持稳定。删除 `001` 后不会重编号 `002`；以后新增
样本会复用最小空闲编号。PUT 替换成功后 `sample_id` 不变，并重算
`centroid.npy`。DELETE 删除非最后一条样本时重算 centroid；删除最后一条时同时删除
身份目录和注册表记录并释放身份槽位。两种变更成功后均同步当前进程声纹检索索引。

PUT、单文件新增与批量新增使用相同的 WAV/VAD/有效语音/16 kHz 归一化、embedding
和跨身份冲突校验。新文件校验失败时
原 WAV、embedding 和 centroid 保持不变。接口只允许下载 WAV，不提供 `.npy` 或
`centroid.npy` 下载端点；这些生物特征模板继续只保存在设备本地。

旧版 `POST /api/v1/speakers`、`PATCH /api/v1/speakers/{name}` 和
`DELETE /api/v1/speakers/{name}` 均保持移除。新的身份级删除位于样本集合路径
`DELETE /api/v1/speakers/{name}/samples`；全库删除为
`DELETE /api/v1/speakers?confirm=true`。删除成功后都会同步运行时索引。

---

## Mock 模式

设置 `mock.enabled: true`、`mock.mode: event` 可直接模拟下游语音事件，
不加载硬件和模型。每轮按 `EVT_VOICE_WAKEUP → 一个同会话语音事件 →
EVT_STATE_CHANGED(state=idle)` 运行，整轮保持同一个 `interaction_id`；正式配置下
不会因空闲或总时长自动终止，必须由 `stop_listening`（或明确的结束流程）结束。

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
