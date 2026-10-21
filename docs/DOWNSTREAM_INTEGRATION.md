# 听觉事件下游开发对接文档

对接基线：2026 年 10 月 15 日，AudioEvent v2。
适用对象：行为树、业务调度、视觉取物和导航模块开发人员。

AudioEvent v3 的事件名称与数据结构见 [语音模块事件与数据结构](DOWNSTREAM_INTEGRATION_V3.md)。

Voice 负责识别语音并发布结构化事件。下游订阅事件，完成业务路由、权限判断、
行为仲裁及执行。**地点点位由调用方自行实现**：Voice 发布的 `location_id` 只是
地点语义标识，不包含地图、坐标或导航结果。

配套交付：[完整命令映射表 CSV](DOWNSTREAM_COMMAND_MAP.csv)、
[全部 ROS2 字段及服务契约](ROS2_CONTRACT.md)、
[Excel逐条测试矩阵](COMMAND_CATALOG_TEST_MATRIX.md)。
本文和 CSV 描述发布端能力，不表示下游已完成对应动作。

## 1 对接范围与开发分工

| 模块 | 负责内容 |
|---|---|
| Voice | 唤醒、录音、KWS、ASR、声纹、固定词库、取物和地点句式、事件发布及语音会话管理 |
| 事件接收与行为调度 | JSON 校验、事件白名单、处理标志检查、目标参数校验、去重、身份策略、优先级与抢占 |
| 视觉与取物调用方 | 将规范物品类别交给视觉搜索，选择目标实例，组织定位、靠近、取物与失败处理 |
| 导航调用方 | 将地点 ID 绑定到当前地图点位，检查点位有效性，发起导航并处理到达、失败和取消 |
| 动作执行方 | 将业务行为映射到实际能力，管理动作状态、完成反馈与硬件限制 |

Voice 不提供目标跟踪 ID、地图坐标、靠近距离、行为优先级或动作完成回执。
`slots.action_name`、`slots.behavior` 是产品行为参考信息，不能据此绕过下游调度
直接调用底盘。`is_executable=true` 只表示发布端允许按动作指令处理；下游仍须
确认自身是否支持该能力以及当前是否允许执行。

## 2 接口与接入顺序

| 方向 | 名称 | ROS2 类型 | 说明 |
|---|---|---|---|
| 订阅 | `/perception/audio_event` | `std_msgs/msg/String` | `data` 为 UTF-8 JSON；主要业务入口 |
| 调用，可选 | `/perception/voice/task` | `marsdog_voice_interaction/srv/VoiceTask` | 查询、开始、结束和保持语音会话 |
| 订阅，可选 | `/perception/voice/enrollment_event` | `std_msgs/msg/String` | 声纹注册进度；使用独立数据结构，不按 AudioEvent 解析 |

事件 Topic 使用 `RELIABLE / KEEP_LAST / depth=10`，持久性为默认 `VOLATILE`。
下游使用相同 QoS；迟订阅不会补发历史事件。需要启动时恢复会话信息时，调用
`get_interaction_state`。双方须使用可互通的 ROS2 网络与 `ROS_DOMAIN_ID`。

接入顺序：

1. 订阅 Topic，解析 `msg.data`，确认 `schema_version=2`；保留原始消息用于诊断。
2. 将唤醒、身份、会话结束事件交给各自处理器；不要对它们套用动作命令过滤规则。
3. 对业务命令检查白名单、处理标志和必要槽位，然后去重并交给业务队列。
4. 下游映射成行为及执行请求，记录独立的执行结果；接收成功与动作成功分别验收。

回调只做轻量解析和入队，导航、视觉请求和长时间动作放到异步任务中。

## 3 公共字段与处理标志

| 字段 | 类型 | 下游用途 |
|---|---|---|
| `schema_version` | int | 当前为 `2`；不支持的版本拒绝执行并记录 |
| `header.stamp` | float | Unix 秒数，不是 ROS `sec/nanosec` 对象；保留作日志关联 |
| `header.frame_id` | string | `microphone_array` |
| `event_type`、`command_id` | string | 业务路由主键；命令事件应校验二者对应关系 |
| `interaction_id` | string | 一次语音会话的 ID |
| `utterance_id` | string | 一句话的 ID；同句的转写、身份和命令事件共享 |
| `wake_id` | string | 每次有效唤醒的 ID；同一会话再次唤醒时更新 |
| `asr_text`、`language` | string | 原始转写与语言；用于展示和排障，不重新做关键词动作触发 |
| `speaker_id` | string | `owner`、`family_member_1`～`family_member_4`、`unknown` 等 |
| `speaker_confidence` | float | 声纹匹配分数，不是身份正确的概率 |
| `dispatch_role` | string | 区分具体命令、社交反应、摘要和诊断 |
| `is_executable` | bool | 是否属于可执行动作命令；社交反应为 `false` |
| `should_trigger_behavior_tree` | bool | 是否授权进入下游行为处理流程 |
| `slots` | array | `[{"key":"...","value":"..."}]`，键和值均为字符串 |
| `intent_source` | string | 如 `command_lexicon`、`kws`、`parameterized_command`；用于诊断 |
| `state`、`state_reason` | string | 配合 `EVT_STATE_CHANGED` 管理会话生命周期 |

未使用的公共字段仍可能存在，值为空字符串、`0`、`false` 或空数组。允许增加不认识
的字段和槽位；不认识的事件不执行。`slots` 不是 JSON 字典，需转换后按键读取，
不能依赖列表顺序。布尔值必须按 JSON 布尔类型判断，不能把字符串 `"false"` 当真值。

| 事件类别 | dispatch_role | is_executable | should_trigger_behavior_tree | 消费方式 |
|---|---|---|---|---|
| 已确认的动作命令 | `specific_command` | true | true | 白名单及参数校验后交给行为调度 |
| 词库夸赞、责备 | `social_reaction` | false | true | 交给明确接入的一次性社交反应处理器 |
| 词库呼名 `CALL_NAME` | `call` | false | false | 不作为动作，也不作为硬件唤醒 |
| 参数句式的未知物品或地点 | `recognition_summary` | false | false | 保留原始名称，记录未支持原因 |
| Model Intent 的 `KNOWN` 摘要 | `semantic_classification` | false | false | 仅展示或记录，不二次执行 |
| `speech`、`UNKNOWN` 等 | 空或诊断角色 | false | false | 转写或诊断，不触发动作 |

动作分支同时要求两个标志为 `true`。社交分支要求 `social_reaction`、
`should_trigger_behavior_tree=true`，并且事件明确在社交白名单中。
不要只看 `EVT_VOICE_COMMAND_` 前缀、`dispatch_role` 或 `control=DO`：
未知地点也可能有 `control=DO`，而 `QUIET` 的语义控制标签可能为 `STOP`。
`intent_confidence=1.0` 表示规则命中，不证明 ASR 转写和物理执行必然正确。

`social/intent/control/raw_nlu_tag/nlu_protocol` 供语义诊断使用。参数句式使用
`intent=FETCH/GO_TO_LOCATION`，`nlu_protocol/raw_nlu_tag` 为空；不要要求所有
事件都能通过 Model Intent 的三轴枚举校验。路由以具体事件及命令编号为准。

## 4 本次必须接入的业务语义

下表是本次重点；[CSV](DOWNSTREAM_COMMAND_MAP.csv) 列出全部 43 个固定路由组及
4 个参数路由、1个闲聊兜底和1个声源入口记录。CSV 的 `standard_phrases` 和 `variants` 用 ` | ` 分隔，
文件编码为 UTF-8 BOM，程序读取可使用 `utf-8-sig`；不展开 2220 条自动扩展。
参数路由行的两个标志描述目标已支持的分支，未知目标
实际发布时均为 `false`。CSV 是本次交付快照，后续以配置和发布代码为准。

| 识别例子 | event_type | command_id | 调用方处理要点 |
|---|---|---|---|
| 走、跟着我、跟上 | `EVT_VOICE_COMMAND_FOLLOW` | `CMD_FOLLOW` | 跟随，不再将“走”解释为随机走开 |
| 去追球 | `EVT_VOICE_COMMAND_CHASE_BALL` | `CMD_CHASE_BALL` | 第37行；接入追球、追到后随机点位放球，最多5分钟；与追人 CHASE、取物 FETCH 分开 |
| 前进、向前走 | `EVT_VOICE_COMMAND_MOVE_FORWARD` | `CMD_MOVE_FORWARD` | 挪动10～30 cm，不复用随机 WALK |
| 站好、站着、站起来 | `EVT_VOICE_COMMAND_STAND_UP` | `CMD_STAND_UP` | 起身站立 |
| 向左转、向右转 | `EVT_VOICE_COMMAND_TURN_LEFT/RIGHT` | `CMD_TURN_LEFT/RIGHT` | 表内80～110°转向，须新增下游映射 |
| 不可以咬、松口 | `EVT_VOICE_COMMAND_DROP` | `CMD_SPIT` | 松口，不再按责备处理 |
| 别动、停、不可以 | `EVT_VOICE_COMMAND_HOLD_POSITION` | `CMD_HOLD_POSITION` | 保持当前姿态，不等同全局急停 |
| 安静、别叫 | `EVT_VOICE_COMMAND_QUIET` | `CMD_QUIET` | 停止发声，不等同底盘急停 |
| 厉害、太厉害了、乖狗狗、小宝贝、小跟屁虫 | `EVT_VOICE_COMMAND_PRAISE` | `CMD_PRAISE` | 按社交分支处理 |
| 我生气了、罚你站着、罚你不准吃饭 | `EVT_VOICE_COMMAND_SCOLD` | `CMD_SCOLD` | 责备反应，不拆出站立或进食动作 |
| 小狗、跟屁虫 | `EVT_VOICE_CALL` | `CMD_CALL_NAME` | 两个标志均 false，不代替唤醒 |
| 去拿球、把球拿来 | `EVT_VOICE_COMMAND_FETCH` | `CMD_FETCH_OBJECT` | 读取并校验规范物品名称 |
| 去房间、到客厅去、回狗窝 | `EVT_VOICE_COMMAND_GO_TO_LOCATION` | `CMD_GO_TO_LOCATION` | 读取地点 ID，由调用方绑定点位；完整句子优先于单字“去” |

固定词库优先于参数句式。“叫两声/说话”统一归 BARK，游戏分别路由。
“捡球”走 FETCH 参数句式；“找玩具/去拿玩具”走可执行 FETCH 语义组，由下游选择具体玩具。
旧版“回家/去/走开/自己去玩吧/翻滚”等独立词条已移出目录。

### 4.1 取物参数

| 槽位 | 含义 |
|---|---|
| `object_name` | 规范视觉类别，区分大小写；语义组或未知目标为字符串 `NONE` |
| `object_group` | 泛称玩具为 `toy`；仅参数句式输出，具体类别目标不携带此槽位 |
| `object_mention` | 句式提取的目标名称，已做规范化；不是整句原文 |
| `object_matched_alias` | 命中的物品别名；已支持目标提供 |
| `object_match_source` | 具体类别为 `asr_rule`，语义组为 `semantic_group`，未支持为 `unsupported` |
| `object_catalog_version` | 具体类别目标的物品词表版本，当前 `2026-10-15-v2` |
| `reason` | 未支持目标为 `unsupported_object` |

[物品词表](../config/object_targets.yaml) 当前有以下 18 个规范类别；中文仅列常用别名。

| 中文别名 | object_name | 中文别名 | object_name |
|---|---|---|---|
| 球 | `dog toy ball` | 飞盘 | `dog frisbee toy` |
| 拔河环、拉环 | `dog tug ring toy` | 项圈 | `dog collar` |
| 狗碗、饭碗 | `dog bowl` | 牵引绳、狗绳 | `dog leash` |
| 零食袋 | `dog treat bag` | 罐头 | `dog food can` |
| 狗窝、狗床 | `dog bed` | 垃圾桶 | `trash can` |
| 纸箱 | `cardboard shipping box` | 袜子 | `sock` |
| 拖鞋 | `slipper` | 纸巾 | `tissue paper` |
| 门 | `door` | 楼梯 | `stairs` |
| 猫 | `cat` | 狗 | `dog` |

这是识别类别表，不是机器人可抓取能力表。下游仍需设置支持的类别和行为，
不能因 Voice 识别出“门、楼梯、猫”等类别就认定能执行叼取。
“玩具”不指定单一视觉类别，以 `object_name=NONE, object_group=toy` 授权找玩具流程，
`is_executable/should_trigger_behavior_tree=true`，不携带 `reason=unsupported_object`。
下游应显式支持 toy 组并选择具体玩具；不要将所有 `object_name=NONE` 都当成玩具，
也不要将字符串 toy 直接传给只接受视觉类别的接口。原事件仍为 FETCH，保留 `object_mention=玩具`。
“咬绳”“玩具箱”仍不支持；“篮球鞋”“球和飞盘”也不会按子串截成球。

### 4.2 地点参数

| location_id | location_name | 可识别别名 |
|---|---|---|
| `dog_bed` | 狗窝 | 狗窝、狗床 |
| `room` | 房间 | 房间 |
| `bedroom` | 卧室 | 卧室、睡房 |
| `living_room` | 客厅 | 客厅 |
| `kitchen` | 厨房 | 厨房 |
| `entrance` | 门口 | 门口 |

槽位还包含 `location_mention` 和 `location_catalog_version=2026-10-15-v2`。
词表见 [location_targets.yaml](../config/location_targets.yaml)。
未知地点使用 `location_id=NONE`，原名称保留在 `location_name/location_mention`，
并携带 `reason=unsupported_location`。

下游建立 `location_id → 当前地图的点位 ID 或位姿` 映射。一个地点虽被 Voice 识别，
但未绑定点位时，下游应记录“未绑定地点”并结束本次调度或发起自身的补充交互。
不使用默认坐标，不把未知地点交给导航。地图切换、同名房间选择和点位维护由调用方负责。

### 4.3 找人和地点取物组合

“去找xx”发布 `EVT_VOICE_COMMAND_FIND_PERSON / CMD_FIND_PERSON`，槽位为
`person_id/person_name/person_mention`，仅 `config/person_targets.yaml` 中登记的身份才可执行。
当前目录仅配置“主人 → owner”，“去找主人”携带 `person_id=owner`、`person_name=主人`，两个执行标志均 true。
未知人物使用 `person_id=NONE` 和 `reason=unregistered_person`，两个执行标志均 false。
此人物是被寻找者，不是公共字段 `speaker_id` 所表示的说话者。
声纹注册不会自动填写本表，已有 owner/family 等声纹槽位也不代表下游能定位同一人物。
`owner` 与视觉身份槽位对齐；下游仍需完成人脸注册和寻人动作接入。目录修改后重启 Voice 加载。

“去厨房拿狗绳子”发布 `EVT_VOICE_COMMAND_FETCH_FROM_LOCATION / CMD_FETCH_FROM_LOCATION`，
同时携带 `location_id=kitchen` 与 `object_name=dog leash` 及原始 mention。
地点和物品类别/语义组必须同时支持；未知者使用 NONE 且不携带受支持的语义组，以不可执行摘要发布，禁止只执行半句。
“去门口拿狗绳”使用 `location_id=entrance, object_name=dog leash`；
“去门口拿玩具”使用 `location_id=entrance, object_name=NONE, object_group=toy`，两者均授权组合任务。
事件槽位的 `catalog_source_rows` 使用实际 Excel 行号：FETCH=42、FIND_PERSON=43、
GO_TO_LOCATION=44、FETCH_FROM_LOCATION=45。

表内的全局速度0.3～0.5、10～30 cm挪动、80～110°转向、调速±5%、
动作池随机选择、单次互动动作不重复超过3次、UWB/电量条件、任务超时和视觉会话条件
由下游实现；原文保存在目录元数据和CSV，不是 Voice 已执行的规则。

## 5 消息示例

以下为合法 JSON 的**字段摘录**，省略默认字段；会话 ID 为示意值。运行时消息包含
[完整公共字段](../marsdog_voice_interaction/messages/audio_event.py)。三个示例分别属于
不同语句，实际 `utterance_id` 不复用。

“把球拿来”：

```json
{
  "schema_version": 2,
  "event_type": "EVT_VOICE_COMMAND_FETCH",
  "command_id": "CMD_FETCH_OBJECT",
  "interaction_id": "session-demo",
  "utterance_id": "utterance-fetch",
  "asr_text": "把球拿来",
  "intent_source": "parameterized_command",
  "dispatch_role": "specific_command",
  "is_executable": true,
  "should_trigger_behavior_tree": true,
  "slots": [
    {"key": "object_name", "value": "dog toy ball"},
    {"key": "object_mention", "value": "球"},
    {"key": "object_matched_alias", "value": "球"},
    {"key": "object_match_source", "value": "asr_rule"},
    {"key": "object_catalog_version", "value": "2026-10-15-v2"},
    {"key": "command_key", "value": "FETCH"},
    {"key": "match_strategy", "value": "sentence_template"}
  ]
}
```

“到客厅去”：

```json
{
  "schema_version": 2,
  "event_type": "EVT_VOICE_COMMAND_GO_TO_LOCATION",
  "command_id": "CMD_GO_TO_LOCATION",
  "interaction_id": "session-demo",
  "utterance_id": "utterance-location",
  "asr_text": "到客厅去",
  "intent_source": "parameterized_command",
  "dispatch_role": "specific_command",
  "is_executable": true,
  "should_trigger_behavior_tree": true,
  "slots": [
    {"key": "location_id", "value": "living_room"},
    {"key": "location_name", "value": "客厅"},
    {"key": "location_mention", "value": "客厅"},
    {"key": "location_catalog_version", "value": "2026-10-15-v2"},
    {"key": "command_key", "value": "GO_TO_LOCATION"},
    {"key": "match_strategy", "value": "sentence_template"}
  ]
}
```

“去办公室”：事件名仍为地点命令，但只能记录，不能执行。

```json
{
  "schema_version": 2,
  "event_type": "EVT_VOICE_COMMAND_GO_TO_LOCATION",
  "command_id": "CMD_GO_TO_LOCATION",
  "interaction_id": "session-demo",
  "utterance_id": "utterance-unsupported",
  "asr_text": "去办公室",
  "intent_source": "parameterized_command",
  "dispatch_role": "recognition_summary",
  "is_executable": false,
  "should_trigger_behavior_tree": false,
  "slots": [
    {"key": "location_id", "value": "NONE"},
    {"key": "location_name", "value": "办公室"},
    {"key": "location_mention", "value": "办公室"},
    {"key": "location_catalog_version", "value": "2026-10-15-v2"},
    {"key": "reason", "value": "unsupported_location"},
    {"key": "command_key", "value": "GO_TO_LOCATION"},
    {"key": "match_strategy", "value": "sentence_template"}
  ]
}
```

“太厉害了”应进入社交反应：`event_type=EVT_VOICE_COMMAND_PRAISE`、
`command_id=CMD_PRAISE`、`intent_source=command_lexicon`、
`dispatch_role=social_reaction`、`is_executable=false`、
`should_trigger_behavior_tree=true`。不要被 `is_executable=false` 误过滤。

## 6 会话关联与去重

硬件唤醒发布 `EVT_VOICE_WAKEUP`，随后唤醒声纹结果通过
`EVT_VOICE_WAKE_SPEAKER_RESULT` 发布。用 `(interaction_id, wake_id)` 关联；
旧 `wake_id` 的迟到结果不能覆盖新唤醒的身份和声源方向。
唤醒事件的 `utterance_id` 为空，不能套用普通语句的去重键。

“这儿／这里这里／到这来”和词库／模型昵称呼唤统一发布 EVT_VOICE_CALL，
call_kind=here|name，intent_source 保留实际识别来源。interaction_id 关联当前会话，wake_id 为空，
utterance_id 标识本句。两个执行标志均为 false；不创建或刷新会话，不启动唤醒声纹。
仅在已有会话中发布，方向无效也保留呼唤通知。

方向读取 doa/doa_valid/doa_age_ms/doa_source；仅使用实时有效 VAD update，ASR 前绑定本句快照。
发布前重算方向年龄，默认 600 ms；无效时 doa=-1，下游跳过转向。header.stamp 为发布时间，
下游还须补充传输和执行排队时间。不要读取 wake_angle 或把该事件当作 WAKEUP／COME。
用 (interaction_id,utterance_id,event_type) 去重；固件／ASR 同次命中 Voice 已按声学时间去重。
旧 CALL_HERE、CALL_NAME、COMMAND_CALL_NAME 名称仅兼容输入，新话题不双发。
Tree 必须显式接入新事件，不能仅改事件名就复用唤醒会话入口。
完整约定见 [统一呼唤](ROS2_CONTRACT.md#统一呼唤2026-10-19)。

一句话可同时产生身份事件、`speech` 和具体命令。这些是同一句话的不同信息，
不能只用 `utterance_id` 整句去重。业务事件采用
`(interaction_id, utterance_id, event_type)` 去重；同样的命令在不同语句中允许再次处理。
唤醒事件采用 `(interaction_id, wake_id, event_type)`。
去重缓存设置容量和有效期；持久任务需要由下游保证“记录去重键与业务入队”的一致性。
ROS2 的 RELIABLE 不等于业务恰好执行一次，进程重启后的执行状态由下游管理。

`EVT_VOICE_UNMASTER_ID` 表示未匹配或历史身份等情况，可能源于没有注册模板，
不能仅凭事件名判定为陌生人。唤醒身份结果须结合 `speaker_role/speaker_status`；
`undetermined` 不等于 `stranger`。正式配置启用逐句声纹，每句依据自身音频识别，
ASR 命令事件使用该句结果。即使主人唤醒，后续家人或 unknown 也按各句结果下发。

`wake_angle` 为麦克风阵列原始角度，单位为度。动作执行方负责且只应用一次安装
零偏和正负方向标定，Voice 不转换为底盘朝向。

会话结束发布 `EVT_STATE_CHANGED`，`state=idle`，原因包括
`interaction_timeout` 或 `stop_listening`。只更新匹配 `interaction_id` 的会话，
旧会话结束不能关闭新会话。**语音会话结束不等同于机器人动作结束或急停**：跟随、
导航是否取消由下游明确制定策略，并等待自身动作系统的终态反馈。

### 通用声源事件

Voice 新增 `EVT_VOICE_SOUND_SOURCE_DETECTED`，不要求唤醒。以 `sound_id` 去重，
读取 `doa/doa_valid/doa_age_ms/sound_trigger_stamp`。
JSON 的 `type=vad,state=update,doa_valid=true` 时直接发布；false 不发布，不等待模型分类。
`should_trigger_behavior_tree=true,is_executable=false,dispatch_role=sound_source`，
下游需新增事件路由后才能触发“转向、靠近、人脸/物体/姿态识别”通用序列。
该事件的会话字段为空，不沿用唤醒主人身份，也没有声源距离。完整协议和时效规则见
[SOUND_SOURCE_EVENTS.md](SOUND_SOURCE_EVENTS.md)。

## 7 最小订阅参考

下面示例使用 ROS2 Humble 的 `rclpy`，只记录通过校验的事件，不调用导航或动作。
示例白名单覆盖本次主要变化，取物示例只接收球、飞盘和纸箱；下游按实际能力扩展。
正式接入时，将“通过接收校验”日志替换为业务入队，并补上身份策略、地点绑定和任务状态管理。
生命周期和身份分支当前仅记录日志，须按第 6 节实现关联处理。

```python
import json
import time
from collections import OrderedDict

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from std_msgs.msg import String


COMMAND_IDS = {
    "EVT_VOICE_COMMAND_FETCH": "CMD_FETCH_OBJECT",
    "EVT_VOICE_COMMAND_GO_TO_LOCATION": "CMD_GO_TO_LOCATION",
    "EVT_VOICE_COMMAND_FETCH_FROM_LOCATION": "CMD_FETCH_FROM_LOCATION",
    "EVT_VOICE_COMMAND_FIND_PERSON": "CMD_FIND_PERSON",
    "EVT_VOICE_COMMAND_FOLLOW": "CMD_FOLLOW",
    "EVT_VOICE_COMMAND_STAND_UP": "CMD_STAND_UP",
    "EVT_VOICE_COMMAND_DROP": "CMD_SPIT",
    "EVT_VOICE_COMMAND_HOLD_POSITION": "CMD_HOLD_POSITION",
    "EVT_VOICE_COMMAND_QUIET": "CMD_QUIET",
}
SOCIAL_IDS = {
    "EVT_VOICE_COMMAND_PRAISE": "CMD_PRAISE",
    "EVT_VOICE_COMMAND_SCOLD": "CMD_SCOLD",
}
CONTEXT_EVENTS = {
    "EVT_VOICE_WAKEUP", "EVT_VOICE_WAKE_SPEAKER_RESULT",
    "EVT_VOICE_MASTER_ID", "EVT_VOICE_FOLK_ID", "EVT_VOICE_UNMASTER_ID",
    "EVT_STATE_CHANGED",
}
OBJECTS = {"dog toy ball", "dog frisbee toy", "dog leash", "cardboard shipping box"}
PERSON_IDS = set()  # 从实际已注册的身份目录读取。
LOCATIONS = {"dog_bed", "room", "bedroom", "living_room", "kitchen", "entrance"}


def select_business(event):
    kind = event.get("event_type")
    if not isinstance(kind, str):
        return None
    if event.get("should_trigger_behavior_tree") is not True:
        return None
    if kind in SOCIAL_IDS:
        valid = (event.get("command_id") == SOCIAL_IDS[kind]
                 and event.get("dispatch_role") == "social_reaction"
                 and event.get("is_executable") is False)
    else:
        valid = (kind in COMMAND_IDS
                 and event.get("command_id") == COMMAND_IDS[kind]
                 and event.get("dispatch_role") == "specific_command"
                 and event.get("is_executable") is True)
    if not valid:
        return None
    slots = {}
    items = event.get("slots", [])
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            return None
        key, value = item.get("key"), item.get("value")
        if (not isinstance(key, str) or not isinstance(value, str)
                or not key or key in slots):
            return None
        slots[key] = value
    if kind in {"EVT_VOICE_COMMAND_FETCH", "EVT_VOICE_COMMAND_FETCH_FROM_LOCATION"}:
        toy_group = (slots.get("object_name") == "NONE"
                     and slots.get("object_group") == "toy"
                     and slots.get("object_mention") == "玩具"
                     and slots.get("object_match_source") == "semantic_group")
        if slots.get("object_name") not in OBJECTS and not toy_group:
            return None
    if kind in {"EVT_VOICE_COMMAND_GO_TO_LOCATION", "EVT_VOICE_COMMAND_FETCH_FROM_LOCATION"} and slots.get("location_id") not in LOCATIONS:
        return None
    if kind == "EVT_VOICE_COMMAND_FIND_PERSON" and slots.get("person_id") not in PERSON_IDS:
        return None
    return slots


class VoiceConsumer(Node):
    def __init__(self):
        super().__init__("voice_consumer_example")
        self.seen = OrderedDict()
        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                         history=HistoryPolicy.KEEP_LAST)
        self.subscription = self.create_subscription(
            String, "/perception/audio_event", self.on_event, qos)

    def on_event(self, message):
        try:
            event = json.loads(message.data)
        except (ValueError, TypeError):
            self.get_logger().warning("非法 AudioEvent JSON")
            return
        if not isinstance(event, dict):
            return
        if type(event.get("schema_version")) is not int or event["schema_version"] != 2:
            return
        kind = event.get("event_type")
        if not isinstance(kind, str):
            return
        if kind in CONTEXT_EVENTS:
            self.get_logger().info(f"会话或身份事件: {event}")
            return
        slots = select_business(event)
        if slots is None:
            self.get_logger().debug(f"不进入业务调度: {event}")
            return
        key = (event.get("interaction_id"), event.get("utterance_id"), kind)
        if not all(isinstance(value, str) and value for value in key):
            return
        now = time.monotonic()
        while self.seen and now - next(iter(self.seen.values())) > 120:
            self.seen.popitem(last=False)
        if key in self.seen:
            return
        # 演示仅记录接收；生产需在可靠入队成功后记录去重键。
        self.get_logger().info(f"通过接收校验: key={key}, slots={slots}")
        self.seen[key] = now
        if len(self.seen) > 1024:
            self.seen.popitem(last=False)


def main():
    rclpy.init()
    node = VoiceConsumer()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
```

## 8 可选会话控制服务

服务类型定义见 [VoiceTask.srv](../srv/VoiceTask.srv)。请求字段为
`task_id/task_type/params_json`，其中 `params_json` 是编码后的 JSON 字符串。
响应包含 `success/task_id/task_type/result_json/error_message/latency_ms`；
先检查 `success`，再解析 `result_json`。服务成功仅表示会话操作成功。

| task_type | params_json 内容 | 语义 |
|---|---|---|
| `get_interaction_state` | `{}` | 查询当前会话 ID、是否监听、剩余租约等 |
| `start_listening` | `{}`，或包含 `expected_interaction_id` | 开始监听；有匹配会话时刷新活动时间；指定过期或不匹配 ID 时失败 |
| `stop_listening` | `{}` | 结束当前会话；当前实现不校验传入的会话 ID，不能当成针对旧会话的条件取消 |
| `hold_interaction` | `interaction_id`、`hold_token`、`lease_sec`、可选 `reason` | 保持指定活跃会话；相同 token 续租 |
| `release_interaction_hold` | `interaction_id`、`hold_token`、布尔 `reset_idle_timer` | 释放租约；仅实际释放有效 token 时可刷新空闲计时 |

正式配置空闲超时 30 秒、总时长上限禁用、单次保持租约最多 20 秒。非空 ASR
即刷新空闲计时，不要求命中命令；后台 ASR 等待期间保持会话有效。
调用方在靠近或对话准备期间可申请例如 6 秒租约并在到期前续租，结束后释放。
保持租约不关闭录音，不屏蔽停止监听，也不代替下游动作任务的存活机制。

只读查询示例：

```bash
ros2 service call /perception/voice/task marsdog_voice_interaction/srv/VoiceTask \
  '{task_id: "query-demo", task_type: "get_interaction_state", params_json: "{}"}'
```

## 9 识别链路与排障

当前正式配置为 KWS 优先、`publish_mode=immediate`。第一次有效 KWS 命中通过
250 ms 否定检查后即发布，同句后续不同关键词不再发布命令，同句不调用意图模型。
没有有效 KWS 发布且未被否定拦截时，ASR 先走完整词库，再走取物/地点句式，
仍未匹配则发布 UNKNOWN；正式配置关闭 RKLLM 和规则意图提供器。
旧 GO_GET_IT 和单字 KWS 已移除；其他短词仍可能在长句完成前触发，整句场景可选 deferred。

即时 KWS 事件按目录定义发布，例如 `EVT_VOICE_COMMAND_COME`。
事件 `intent_source=kws`、`asr_text=""`，slots 中 `recognition_strategy=kws_immediate`；
后续完整 ASR 不再发同句业务命令。下游沿用现有 COME 事件入口，不能等待 `speech` 才处理此事件。
即时命令身份为 unknown/pending；每句在 VAD 完成后独立核验声纹，
以相同 `interaction_id/utterance_id` 的身份事件关联该句识别结果。
Voice 发布不证明行为树已调度或底盘已执行。

下游统一消费最终事件，不按 `intent_source` 再执行一次，也不对 `speech.asr_text`
自行做包含匹配。任意包含“厉害”的长句不会自动视为夸赞。否定句不能截掉否定词
后执行；精确登记的“别动、别叫”等控制词及责备表达按各自完整命令处理。

| 现象 | 首先检查 |
|---|---|
| 有 `speech`，随后 `UNKNOWN` | ASR 已转写，继续查完整句子是否命中词库或参数句式；不等于麦克风未识别 |
| “太厉害了”仍是 UNKNOWN | 启动日志中的词库是否包含补充变体；词库启动时加载，确认新配置已加载并重启 |
| 有 PRAISE，行为端无反应 | 是否误用 `is_executable` 单一过滤，是否接入社交白名单和映射 |
| 有地点事件，未发起导航 | 两个标志、`location_id`、本地地图绑定及导航服务状态 |
| 有 FETCH，未找到目标 | 规范 `object_name`、下游支持范围、视觉识别和搜索结果分别排查 |
| 动作重复 | 比较 interaction、utterance、event 三元组；检查是否同时消费 speech、摘要或多次入队 |
| 收到 UNMASTER | 查声纹模板、分数及拒识原因；不能由此推断命令路由失败 |

联调只读检查：

```bash
source /opt/ros/humble/setup.bash
source /home/cat/marsdog_ws/install/local_setup.bash
ros2 pkg prefix marsdog_voice_interaction
ros2 topic info -v /perception/audio_event
ros2 topic echo /perception/audio_event
ros2 service type /perception/voice/task
```

使用实际部署工作空间替换示例路径。记录 `runtime_start` 的配置路径、Provider、
词库版本与数量；本次固定词库基线为 43 组、222 条标准词句、123 条人工变体、
2565 个精确入口。词库版本为 `2026-10-16-workbook-v14`，保留“太厉害了”等实用变体，并按新版表重组情绪、游戏和移动事件；同时核对实际加载的文件路径和统计，避免使用旧配置。
完整日志取证见 [TESTING_LOG_GUIDE.md](TESTING_LOG_GUIDE.md)。

## 10 下游联调验收清单

表中“交给调度”只验证事件消费；实际动作和导航另行验收。

| 输入或注入事件 | 发布端预期 | 下游预期 |
|---|---|---|
| 走 | FOLLOW | 不映射为 WALK |
| 站好、站着 | STAND_UP | 起身事件，不映射为保持站立 |
| 不可以咬 | DROP | 松口事件，不触发责备 |
| 太厉害了 | PRAISE，false/true | 进入社交反应一次 |
| 我生气了 | SCOLD，false/true | 进入责备社交反应一次 |
| 罚你站着 | SCOLD，false/true | 社交反应，不执行 STAND_UP |
| 小狗 | CALL_NAME，false/false | 不执行、不作为硬件唤醒 |
| 把球拿来 | FETCH，`object_name=dog toy ball` | 校验类别后交给取物流程 |
| 去拿玩具、去找玩具 | FETCH，true/true，`object_group=toy` | 进入找玩具流程，由下游选择具体玩具 |
| 去拿篮球鞋、去拿玩具箱、把球和飞盘拿来 | FETCH 摘要，false/false | 保留名称，不执行单一目标取物 |
| 去门口 | GO_TO_LOCATION，`entrance` | 使用已绑定门口点位 |
| 去门口拿狗绳、去门口拿玩具 | FETCH_FROM_LOCATION，`entrance` | 保留具体类别或 toy 组，进入组合任务 |
| 到客厅去 | GO_TO_LOCATION，`living_room` | 读取调用方绑定的点位；未绑定则不发导航 |
| 去办公室 | GO_TO_LOCATION 摘要，false/false | 记录未支持地点，不使用默认点位 |
| 不要去拿球、不要去客厅 | 不授权相应取物或地点动作 | 不从转写中提取片段执行 |
| 去厨房拿狗绳子 | FETCH_FROM_LOCATION | 地点和物品同时校验后再交给组合任务 |
| 去找主人 | FIND_PERSON | person_id=owner；当前仅配置主人，下游仍需接入寻人动作 |
| 去找爸爸 | FIND_PERSON | 未登记人物不可执行，reason=unregistered_person |
| Flex 方向年龄超过 600 ms，唤醒事件本身仍新鲜 | WAKEUP，`wake_angle=-1, wake_angle_valid=false`；正常启动录音 | 接收会话与身份，跳过声源转向/接近 |
| 同一命令消息重复投递 | 相同三元组 | 只入队一次 |
| 同一命令说两次 | 不同 utterance_id | 允许两次进入后续业务仲裁 |
| 同句 speech 加具体事件加摘要 | 共用 utterance_id | 仅授权的具体事件进入动作分支 |
| 缺目标槽位、标志类型错误、未知 schema | 通过离线消息注入测试 | 拒绝执行并记录原因 |
| 旧会话 idle 或旧 wake_id 结果迟到 | ID 与当前上下文不匹配 | 不覆盖当前会话或身份 |
| 有效保持租约及释放 | 查询状态反映租约变化 | 按时续租、释放，不用停止监听代替取消动作 |

下游交付至少包含：事件与行为白名单、物品能力表、地点绑定配置、去重与会话处理、
异步执行结果记录，以及上述用例的消息和处理日志。一次端到端验收应能用
`interaction_id/utterance_id` 串起 Voice 发布、下游接收、行为选择、执行请求和最终结果。

源码依据：[事件归一化](../marsdog_voice_interaction/messages/audio_event.py)、
[固定词库路由](../marsdog_voice_interaction/core/command_lexicon.py)、
[参数句式路由](../marsdog_voice_interaction/core/parameterized_commands.py)、
[节点发布与服务实现](../marsdog_voice_interaction/nodes/voice_interaction_node.py)、
[正式配置](../config/voice.yaml)。
