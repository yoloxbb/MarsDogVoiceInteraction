# 语音项目测试与日志取证指南

本文档直接交给测试人员执行。它规定运行模式、日志字段、取证方法、功能判定和
测试报告内容。ROS2 字段的完整定义仍以 [ROS2_CONTRACT.md](ROS2_CONTRACT.md)
为准；本文只定义“怎样测、看什么、交付什么”。

## 1. 测试证据和判定原则

一次有效测试至少保留四类证据：

1. 用例信息：用例 ID、时间、代码版本、配置文件、设备和操作步骤。
2. 节点日志：启动信息、阶段结果、阶段耗时、事件发布和错误。
3. ROS2 接口原文：`/perception/audio_event`、注册 Topic 或 VoiceTask 返回值。
4. 结论：预期、实际、PASS/FAIL、最早异常时间和关联 ID。

Topic/Service 原文是接口结果的权威证据，`VOICE_TRACE` 用于定位链路和耗时；不能
只凭一条普通描述日志判定功能成功。跨模块问题按
`Voice event_publish → Tree candidate_inject/select → Action goal/result` 继续追踪，
Voice 日志不能证明动作已经执行。

所有语音链路按下面两个 ID 关联：

- `interaction_id`：一次唤醒到 `EVT_STATE_CHANGED(state=idle)` 的完整会话。
- `utterance_id`：会话中的一句话；同一句的 KWS、声纹、speech 和意图事件共用。

## 测试团队表格的当前版本口径

测试表描述功能目标，但**不定义意图协议格式**。意图输出必须以
[ROS2_CONTRACT.md](ROS2_CONTRACT.md) 为准，当前正式字段是
`social/intent/control/event_type/command_id/dispatch_role/slots`；
`emotion/action` 仅为兼容字段。测试表中的
`{"action":"fetch","target":"ball"}` 仅是示意，不要求程序输出该结构；物体
指令当前使用 `action=BRING/FETCH`，物体名称放在
`slots=[{"key":"object_name","value":"..."}]`。普通动作不要求目标字段。

### 当前能力基线

代码和配置中的可验证库存为：

- 完整确定性词库：`config/command_catalog.yaml` 覆盖产品表 **116 条源数据**
  （不含表头），归并为 **81 个路由组、155 条标准中文词/句**；每条标准入口生成
  10 条受控扩展，共 **1550 条扩展、1705 条运行时精确匹配入口**。ASR 文本
  命中标准入口或受控扩展后不经过意图模型。19 组核心先发不可执行的
  `EVT_VOICE_COMMAND_KNOWN` 摘要，再发目录指定的具体事件；测试人员按
  [COMMAND_CATALOG_TEST_MATRIX.md](COMMAND_CATALOG_TEST_MATRIX.md) 逐条对齐短语和事件。
- 116 条源数据中 72 条已明确 `ACT_*`，目录保留原始动作名和“具体行为”全文；
  其余行按呼名、夸赞、责备或同类生理/娱乐语义归并，不伪造未定义的 `ACT_*`。
- 19 组核心训练指令是完整词库的子集。当前行为树已有 11 个核心动作映射；
  `EVT_VOICE_CALL_NAME` 已有路由，`PRAISE/SCOLD` 进入情绪链路。按当前源码可确认
  81 个路由组中 14 个有现成下游入口，其余 67 个仍需 Tree/Action 补齐映射。
- 目录外文本使用 Model K `SOCIAL|INTENT|CONTROL` 三轴协议，模型文件为
  `qwen2_5_5b_rk3588_260829_w8a8.rkllm`。模型先产生不可执行的业务大类；19 个
  无歧义白名单组合会额外产生一个可执行具体动作及不可执行 KNOWN 摘要。不能用模型
  INTENT 数量替代词库覆盖率。
- `FETCH/FIND_TOY` 还必须经过 `config/object_targets.yaml` 的 18 类目标物门控；
  未命中目标时 `object_name=NONE`，不得发布可执行 FETCH。
- 流式 KWS 配置有 13 条中文和 13 条英文，共 26 条关键词，当前对应 12 个不同动作
  标签。其中 11 个与核心目录重合，`WAIT` 是兼容 KWS 指令；“回来/COME BACK”已
  统一映射为 `COME`，不再映射 `RETURN`。短句仲裁阈值不会自动扩充关键词；当前
  不把“走、去、来、停”等单字词加入 KWS，单字输入仍按 ASR 目录验收。
- 本地规则意图：30 条正则规则。规则数量不等同于自然语言词条数量。
- 产品表附件共 117 行是“1 行表头 + 116 行数据”，不应记成 117 组指令。
  目录保留 138 条英文参考表达，但由于存在跨分类重复，当前只作元数据，
  不参与确定性直接匹配。

本轮允许指令功能缺失，统一使用以下结果状态：

- `PASS/FAIL`：当前已实现且实际执行的测试项。
- `N/A-MISSING_ACCEPTED`：清单中的指令当前未实现，本轮允许缺失，不计入识别准确率
  分母，但必须进入缺失清单。
- `BLOCKED-MANIFEST`：缺少测试词条、期望标签或音频样本，无法执行或复核。
- `KNOWN-GAP`：已确认功能未实现，只做现状记录，不作为本轮发布阻断项。

报告必须同时给出两个指标，不能只报实现子集的准确率：

```text
功能覆盖率 = 当前已实现指令数 / 目标指令数
识别准确率 = 已实现且实际执行的成功次数 / 已实现且实际执行的总次数
```

### 九项测试的可执行判定矩阵

| 编号 | 当前测试口径 | 日志与接口证据 | 判定标准 |
|---:|---|---|---|
| 1 | 以 `command_catalog.yaml` 中 `core=true` 的 19 组为核心清单，每组代表短语播放 20 次，并补测全部别名。 | 同一句依次出现 ASR、`command_lexicon`、`recognition_arbitration`、不可执行 KNOWN 摘要和预期具体事件；检查两者的 `dispatch_role/specific_event_type/raw_nlu_tag`。 | 每组代表短语至少 17/20，且 19/19 均有结果。KNOWN 摘要不得触发行为树，只有具体事件用于动作；同一句只能有一个识别来源的业务结果。只有现成 Tree/Action 映射的组可判端到端 PASS。 |
| 2 | 以产品表 116 条数据、155 条标准中文词/句和每条 10 个受控扩展执行覆盖测试。不再要求测试方另外提供“117 组”清单。 | 记录 `command_lexicon` 的 `matched/command_key/event_type/match_strategy/catalog_phrase/matched_phrase/expansion_profile/expansion_rule`，并复核事件 slots；只有未命中时才记录模型来源。 | 标准词/句每条测试 10 次时，85% 门限至少 **9/10**；扩展规则自动验收 `1550/1550`，人工按五个 profile 和 19 组核心抽样。同时报告源数据 `116/116`、路由组 `81/81`、标准词/句 `155/155`、总入口 `1705`；下游未映射项不得判端到端 PASS。 |
| 3 | 使用唤醒词启动正式会话，并分别播放一条目录内指令和一条目录外语义文本。 | 两条都应有 ASR；目录内指令随后 `command_lexicon result=matched` 且不出现该句 `stage=intent`；目录外文本 `result=no_match` 后才出现 `stage=intent`。 | 同一 `interaction_id` 内两条链路各自完整且无 ERROR。仅有唤醒事件不能证明后续模块已工作。 |
| 4 | 机播已知文本，对照 `speech` 事件中的 `asr_text` 和完整 `payload`。 | `stage_complete stage=asr result=ok`；`event_publish event_type=speech`；完整 ROS2 原文。 | `asr_text` 与期望文本一致或符合用例允许的等价转写；JSON 字段符合当前 ROS2 契约。测试表里的 `action/target` 不作为格式标准。 |
| 5 | 对编号 4 的同一 `utterance_id` 检查最终路由结果。 | 目录命中看 `stage=command_lexicon`；目录未命中才看 `stage=intent` 的 `social/intent/control/event_types`。 | 核心目录命中必须得到 KNOWN 摘要及指定具体事件；模型结果必须符合三轴组合约束。白名单命令按“大类 → 具体动作 → KNOWN 摘要”发布，只有具体动作可执行。 |
| 6 | 对已有中英文 KWS 逐条执行，并对完整中文 ASR 标准词/句与扩展分层执行；两类覆盖率分开报告。 | KWS 先看 `stage=kws result=candidate`，再看 `recognition_arbitration selected_source/reason` 及最终事件来源；目录看 `intent_source=command_lexicon` 和 `match_strategy`。 | KWS 按 26 条配置逐条验收，不将“26 条关键词”误写成 26 组。短指令可选择 KWS；长句含关键词必须选择 ASR；两条链路不得同时发布业务结果。目录按 155 条标准入口及 1550 条自动扩展验收。138 条英文参考项当前不做确定性直发验收。 |
| 7 | 在一个会话内连续播放 3 条指令，每条之间保留正常句尾静音；既测试三条不同指令，也测试同一指令连续 3 次。 | 一个 `interaction_id` 下出现 3 个不同 `utterance_id`；逐句检查 `recognition_arbitration`、`utterance_complete`、目录匹配和最终事件。 | 三句均正确；每句只允许 KWS 或 ASR 链路中的一个来源发布业务结果。核心命令允许 KNOWN 摘要加具体事件，两者属于同一个结果组。 |
| 8 | 使用相似音、否定反转和未配置的前后缀探索拒识，例如“官过来”“你要不要过来”“不要坐下”。 | 记录 KWS、ASR、`command_lexicon matched/no_match`、`match_strategy`、意图阶段和任何可执行事件。 | 目录只能命中标准词/句或配置明确生成的扩展，不能因子串命中；“请你坐下”应命中，但“不要坐下”“请你不要坐下”不得命中 SIT。流式 KWS 的相似音拒识仍属 `KNOWN-GAP`。 |
| 9 | 播放陌生词，并在最后一次有效 ASR 文本后等待至少 10 秒。 | 先看到 `command_lexicon result=no_match`，再看 Model K 三轴及 `event_types`，最后出现同会话 idle 和 `interaction_end`。 | 合法 OOS `NONE|NONE|NONE` 发布不可执行的 `EVT_VOICE_NEUTRAL`；只有模型与规则均无有效协议结果才发非执行 UNKNOWN 诊断。不发布可执行动作、不崩溃，约 10 秒后待机。 |

### ASR 同音误识别由 KWS 正确路由时如何记分

ASR 转写准确率与最终命令功能必须分项统计，同一个 `utterance_id` 可以出现“ASR
单项 FAIL、命令路由 PASS”。典型用例是用户说“击掌”，`speech.asr_text=机长`，但
KWS 已产生唯一 `HIGH_FIVE` 候选：

```text
stage_complete stage=kws result=candidate
  command_key=HIGH_FIVE event_type=EVT_VOICE_COMMAND_HIGH_FIVE
stage_complete stage=asr result=ok
  speech.asr_text=机长
stage_complete stage=command_lexicon result=no_match
stage_complete stage=recognition_arbitration result=kws_selected
  selected_source=kws reason=short_asr_kws_preferred
event_publish event_type=EVT_VOICE_COMMAND_KNOWN intent_source=kws
event_publish event_type=EVT_VOICE_COMMAND_HIGH_FIVE intent_source=kws
utterance_complete result=published_kws_selected
```

以上证据完整且没有第二个识别来源的业务结果时：

- 编号 4 的 ASR 转写单项记 **FAIL（“击掌”误识别为“机长”）**；
- 编号 5 的最终命令路由和编号 6 的 KWS/ASR 仲裁记 **PASS**；
- 不得将本次记为 `command_lexicon catalog_exact` 成功，也不得因 ASR 文本错误而把已经
  正确发布的 `EVT_VOICE_COMMAND_HIGH_FIVE` 判为 Voice 命令功能失败；
- 若缺少正确 KWS 候选、存在多个不同 KWS 候选、ASR 命中了冲突目录事件、最终事件
  错误或重复发布，则命令路由不能 PASS。

### 测试常用固定日志关键字

测试表中要求的“SDK 日志关键字”统一使用下列稳定字段，不依赖第三方 SDK 的临时
中文描述：

```text
VOICE_TRACE {"record":"runtime_start"...}
VOICE_TRACE {"record":"interaction_start"...}
VOICE_TRACE {"record":"stage_start","stage":"vad_capture"...}
VOICE_TRACE {"record":"stage_complete","stage":"asr"...}
VOICE_TRACE {"record":"stage_complete","stage":"command_lexicon"...}
VOICE_TRACE {"record":"stage_complete","stage":"recognition_arbitration"...}
VOICE_TRACE {"record":"stage_complete","stage":"intent"...}
VOICE_TRACE {"record":"event_publish"...}
VOICE_TRACE {"record":"utterance_complete"...}
VOICE_TRACE {"record":"interaction_end"...}
```

每个 `event_publish` 都带完整的 `payload`，可直接从日志复核 ROS2 JSON；正式验收仍
应同时保存 `/perception/audio_event` 原文，防止只验证了日志而没有验证传输接口。

确定性词库建议使用以下逐条记录格式；核心用例使用 `CORE-*`，全量词库用例另使用
`CATALOG-*` 并记录对应的 `source_rows`。当前 155 条短语的期望值已经整理在
[COMMAND_CATALOG_TEST_MATRIX.md](COMMAND_CATALOG_TEST_MATRIX.md)：

| 指令 ID | 播放文本 | 期望 COMMAND_KEY | 期望 EVENT_TYPE | 期望路由 | Voice 状态 | 下游状态 | 计划次数 | 成功次数 | 结果 |
|---|---|---|---|---|---|---|---:|---:|---|
| CORE-008 | 坐下 | SIT | EVT_VOICE_COMMAND_SIT | command_lexicon | IMPLEMENTED | MAPPED | 20 |  |  |
| CORE-019 | 安静 | QUIET | EVT_VOICE_COMMAND_QUIET | command_lexicon | IMPLEMENTED | KNOWN-GAP | 20 |  |  |

一次“确定性指令识别成功”必须满足：同一个 `utterance_id` 得到
`command_lexicon result=matched`，最终 `command_id/event_type` 符合目录，并且期间
没有发布错误的可执行动作。核心目录必须有 KNOWN 摘要，且摘要
`should_trigger_behavior_tree=false`。ASR 目录被选中后不应再出现该句 `stage=intent`；
KWS 被选中时不应再发布 `command_lexicon` 或 Model K 的业务事件。只有
`speech.asr_text` 正确但目录未命中或事件错误，仍记为失败。各组必须分别达到门限。
目录外文本使用 `social/intent/control` 意图判定表。

## 2. 运行模式

| 模式 | 配置 | 是否需要硬件/模型 | 主要用途 |
|---|---|---:|---|
| 正式链路 | `config/voice.yaml` | 是 | 真机唤醒、VAD、KWS、ASR、声纹、RKLLM 验收 |
| Event Mock | `config/voice.mock.yaml` | 否 | 直接生成完整 ROS2 事件，验证 Topic 契约和下游消费 |
| Pipeline Mock | `config/voice.pipeline.mock.yaml` | 否 | 走 Mock 唤醒、录音、ASR、声纹和规则意图，验证节点编排与耗时日志 |

`runtime_start.runtime_mode` 是所选模式；`runtime_start.providers` 才是本次进程
实际加载的 Provider。正式配置中部分模型 Provider 不可用时，当前实现可能回退到
Mock Provider，因此真机用例必须确认 `providers` 中没有意外的 `Mock*Provider`。
发现意外回退时，该用例记为环境/启动失败，不能作为真机 PASS 证据。

当前 FastAPI 不包含身份验证，局域网请求不带认证请求头。测试环境必须是可信开发
网络；生产认证不在本轮验收范围内。

## 3. 日志输出和级别

配置项位于 `logging`：

| 字段 | 默认值 | 含义 |
|---|---:|---|
| `level` | `INFO` | `INFO` 保留测试证据；`DEBUG` 增加轮询、VAD 和 Provider 细节 |
| `dir` | `../log` | 相对于当前 YAML 文件目录解析后的文件输出目录 |
| `console` | `true` | 同时输出到终端 |
| `file` | `true` | 写入独立进程日志文件 |
| `event_trace` | `true` | 输出固定格式的测试追踪记录 |

每次启动创建一个文件：

```text
<log_dir>/voice_interaction_YYYYMMDD_HHMMSS_<pid>.log
```

启动时的 `runtime_start.log_file` 会给出本次准确路径。Launch 参数
`log_level`、`log_dir` 可覆盖配置，例如：

```bash
ros2 launch marsdog_voice_interaction voice.launch.py \
  config_path:=/home/cat/xbb/MarsDogVoiceInteraction/config/voice.yaml \
  log_level:=DEBUG \
  log_dir:=/tmp/marsdog_voice_qa/VOICE-001
```

日志分两类：

- 普通日志：便于人阅读的 Provider 启停、模型错误、串口/VAD/ASR信息。
- `VOICE_TRACE {JSON}`：字段稳定、每条一行，测试记录和自动提取只依赖这一类。

## 4. `VOICE_TRACE` 记录表

| `record` | 产生时机 | 核心字段 | 测试用途 |
|---|---|---|---|
| `runtime_start` | 节点就绪 | `runtime_mode/providers/command_lexicon/object_target_routing/kws_arbitration/speaker_api/config_path/log_file` | 确认模式、配置、指令目录、目标物目录、KWS 仲裁策略、真实 Provider 和 API 状态 |
| `interaction_start` | 会话开始 | `source/interaction_id/state` | 确认唤醒或 Service 建立会话 |
| `stage_start` | 开始收音 | `stage/interaction_id/utterance_id` | 确认一句话的计时起点 |
| `stage_complete` | 阶段结束 | `stage/result/latency_ms` | VAD、KWS、ASR、声纹、确定性目录、意图耗时与结果 |
| `event_publish` | 发布音频事件 | `event_type/interaction_id/utterance_id/state/payload` | 用完整 payload 对照 Topic 原文和下游入口 |
| `utterance_complete` | 整句处理结束 | `result/event_type/selected_source/latency_ms` | 判断最终路由、发布来源和整句结果 |
| `service_complete` | VoiceTask 返回 | `task_id/task_type/result/latency_ms/task_result` | Service 成败与耗时 |
| `interaction_hold` | 租约申请、续租、释放或到期 | `operation/result/hold_token/reason` | 验证保持租约生命周期 |
| `enrollment_publish` | 发布注册进度 | `result/speaker_id/latency_ms` | 声纹注册阶段结果 |
| `speaker_api_upload` | 上传文件进入声纹业务处理后结束 | `result/speaker_name/audio_valid/has_effective_speech/source_duration_ms/speech_duration_ms/segment_count/latency_ms` | 判断 WAV 内容解析、VAD 截取、声纹提取和落盘结果；不代表完整 HTTP 请求耗时 |
| `speaker_management` | 查询、身份槽位变更或删除 | `operation/result/speaker_name/speaker_count/latency_ms` | 复核声纹管理和运行时索引同步；槽位变更的内部 `operation` 仍为 `rename` |
| `interaction_end` | 会话结束 | `reason/interaction_id/state` | 确认超时或手动停止 |

### 4.1 通用字段

| 字段 | 类型 | 含义和判定方法 |
|---|---|---|
| `record` | string | 追踪记录类型，是筛选日志的第一关键字。 |
| `result` | string | 本次记录的结果。值由不同 `record/stage` 定义，不能跨阶段混用。 |
| `interaction_id` | string | 会话关联 ID；同一轮唤醒到待机期间保持不变。 |
| `utterance_id` | string | 单句关联 ID；同一句的 VAD、KWS、ASR、声纹、意图和事件应一致。 |
| `latency_ms` | number | 当前记录定义范围内的墙钟耗时，单位毫秒；不同记录的起止点见下表。 |
| `error` | string | 失败原因。成功时通常为空并被日志层省略。 |
| `payload` | object | 完整业务对象；用于复核 Topic 或注册结果的原始字段。 |

`VOICE_TRACE` 为减少日志长度，会省略值等于空字符串的可选字段。因此顶层没有
`error/asr_text/speaker_id` 不一定表示日志结构错误，应先结合 `record/result` 判断。
`event_publish.payload` 始终包含完整的 v1 音频事件模板，即使某些字段使用空字符串、
`0.0`、`false` 或空数组作为默认值。

日志行前缀中的 `YYYY-MM-DD HH:MM:SS` 是事件写日志的墙钟时间，用于跨记录计算时序；
JSON 内的 `header.stamp` 是 ROS2 事件时间戳，用于与 Topic、rosbag 和下游事件对齐。

### 4.2 `runtime_start` 字段

| 字段 | 类型 | 含义和判定方法 |
|---|---|---|
| `result` | string | 当前为 `ready`，表示节点初始化流程结束；不代表每个 Provider 都可用。 |
| `runtime_mode` | string | `production`、`mock_event` 或 `mock_pipeline`。 |
| `config_path` | string | 本次节点实际读取的配置文件参数。 |
| `log_level` / `log_file` | string | 实际日志级别和本进程日志文件路径。 |
| `audio_topic` / `enrollment_topic` / `service` | string | 实际发布 Topic 和 VoiceTask Service 名称。 |
| `idle_timeout_sec` | number | 最后一次有效语音后回到待机的超时秒数。 |
| `providers` | object | 每个 Provider 的 `class/available`；正式测试要求真实 Provider 可用且没有意外 Mock。 |
| `command_lexicon` | object | 词库实际加载状态和统计。正式与 Pipeline Mock 应为 `ready=true/command_count=81/core_command_count=19/phrase_count=155/expansion_enabled=true/variants_per_phrase=10/expanded_phrase_count=1550/total_match_phrase_count=1705/expansion_profile_count=5/reference_phrase_count=138/source_row_count=116/covered_source_row_count=116`；`phrase_count` 只统计标准词/句，`total_match_phrase_count` 才是运行时总入口。 |
| `object_target_routing` | object | 目标物目录加载状态。应为 `enabled=true/ready=true/target_count=18`，并记录 `catalog/version/alias_count`。不可用时所有找物模型结果均不得生成具体 FETCH。 |
| `kws_arbitration` | object | 实际仲裁策略。当前必须为 `publish_mode=deferred/arbitration_mode=exclusive`；默认 `asr_long_text_wins=true/kws_fallback_on_asr_empty=true/short_max_chars_zh=2/short_max_words_en=2`。 |
| `speaker_api` | object | `enabled/ready/address/docs`；启动失败时包含 `error`。 |

### 4.3 `stage_complete` 字段和耗时边界

| `stage` | `result` 可能值 | 阶段专属字段 | `latency_ms` 的准确范围 |
|---|---|---|---|
| `vad_capture` | `voice/silence` | `audio_duration_ms` | 从分配本句并启动收音，到 VAD 结果被节点取出；包含等待说话、有效语音、句尾静音和线程轮询，不是纯 VAD 模型推理耗时。 |
| `kws` | `candidate/rejected_catalog_mismatch` | `event_type/command_key/candidate_count/published_event_types` | 从本句开始收音到 KWS 候选首次命中并取出；不是单个 KWS 模型调用耗时。`candidate` 时 `published_event_types=[]`，表示只缓存、尚未发布业务事件。未命中时没有该记录。 |
| `asr` | `ok/empty/error` | `language/text_length` | 一次 `transcribe()` 调用的总耗时。`ok` 仅表示得到非空文本，不表示文本一定正确。 |
| `speaker` | `matched/unknown/error` | `speaker_id/speaker_confidence` | 声纹 embedding 提取、已注册人员检索和阈值判定的总耗时。 |
| `command_lexicon` | `matched/no_match/unavailable` | `command_key/event_type/catalog_version/catalog_phrase/matched_phrase/match_strategy/expansion_profile/expansion_rule/social/intent/control/action_name/source_rows/core/emit_known_event` | 一次规范化及精确哈希查找的总耗时，`latency_ms` 保留三位小数以记录微秒级查找；原词为 `catalog_exact`，受控扩展为 `rule_expansion`；核心匹配要求 `emit_known_event=true`；`matched` 后跳过意图模型，`no_match` 后才进入 `intent`。 |
| `recognition_arbitration` | `kws_selected/asr_selected/none_selected` | `selected_source/reason/asr_text/asr_text_length/asr_is_short/kws_candidate_count/kws_candidate_keys/kws_candidate_event_types/catalog_event_type` | 纯规则仲裁函数的耗时。常见 `reason` 为 `no_kws_candidate/short_asr_catalog_agrees/short_asr_kws_preferred/long_asr_text/asr_catalog_conflicts_with_kws/empty_asr_single_candidate/multiple_kws_candidates`。 |
| `object_target` | `matched/unsupported/unavailable` | `object_name/object_mention/object_matched_alias/object_catalog_version` | 只在 `FETCH/FIND_TOY` 出现。对 ASR 原文进行最长别名优先匹配；只有 `matched` 允许生成可执行 FETCH。 |
| `intent` | `parsed/fallback_unknown` | `event_types/social/intent/control/intent_source` | `_parse_intent()` 与事件路由总耗时；可能是 Model K，或模型无有效输出后兼容规则的累计时间。`NONE|NONE|NONE` 的 `event_types` 应为 `["EVT_VOICE_NEUTRAL"]`。 |

补充判定规则：

- `audio_duration_ms` 是交给 ASR/声纹的结果音频长度，不是 VAD 阶段耗时。
- `intent_source` 常见值为 `command_lexicon/kws/rkllm_model_k/`
  `rule_model_k_compatible/invalid_protocol_fallback`。其中
  `command_lexicon` 和 `kws` 都是模型外的确定性来源。
- 当前 `speaker_confidence` **不是真实余弦相似度**：匹配成功通常为配置阈值加
  `0.3`，未匹配为 `0.0`。它只能辅助判断 `matched/unknown`，不能用于声纹阈值标定、
  距离对比或准确率曲线。声纹是否通过以 `result`、`speaker_id` 和身份事件为准。

### 4.4 `event_publish` 顶层字段

| 字段 | 类型 | 含义和判定方法 |
|---|---|---|
| `topic` | string | 实际发布目标，正常为 `/perception/audio_event`。 |
| `event_type` | string | 本次发布的事件类型，是下游路由的主要判定字段。 |
| `interaction_id` / `utterance_id` | string | 会话和单句关联 ID；会话级事件允许 `utterance_id` 为空。 |
| `state` / `previous_state` / `state_reason` | string | 发布后的状态、前一状态及状态变化原因。 |
| `wake_word` | string | 命中的唤醒词。 |
| `wake_angle` | number | 唤醒方位角，单位度；Voice 不应用安装偏移。 |
| `wake_confidence` / `wake_score_raw` | number | 归一化唤醒置信度和硬件原始分数；原始分数只在完整 `payload` 中保证可见。 |
| `asr_text` / `language` | string | 清洗后的 ASR 文本和语言标识。 |
| `speaker_id` / `speaker_confidence` | string / number | 固定身份 `owner`、`family_member_1`～`family_member_4` 或 `unknown`，以及当前实现的匹配指示值；历史自由名称按 UNMASTER 处理。 |
| `social` / `intent` / `control` | string | Model K 正式三轴；目录核心指令也携带规范三轴，例如 QUIET 为 `NONE|BARK|STOP`。 |
| `emotion` / `action` | string | 兼容字段；模型事件分别镜像 `social/intent`，具体目录事件的 `action` 保留 `command_key`。新测试不得把它们当正式三轴。 |
| `command_id` / `intent_category` / `intent_source` | string | 命令标识、分类类别及决策来源；目录事件要求 `command_id` 等于目录声明值、`intent_source=command_lexicon`。 |
| `intent_confidence` | number | 意图 Provider 给出的置信度；不能与声纹或唤醒置信度混用。 |
| `nlu_protocol` / `raw_nlu_tag` | string | 协议版本及严格校验后的完整三轴文本。Model K 当前不提供校准置信度，不能用 `intent_confidence=0.0` 判失败。 |
| `specific_event_type` / `dispatch_role` | string | KNOWN 摘要指向的具体事件，以及该事件是摘要、具体指令、语义分类还是诊断。 |
| `slots` | array | `[{"key":"...","value":"..."}]`；找物目标使用规范视觉类别 `object_name`。未命中为 `NONE`，同时检查 `object_mention/object_match_source`。 |
| `is_executable` | bool | 当前事件是否表示可执行意图；完整值在 `payload` 中。 |
| `should_trigger_behavior_tree` | bool | 可执行指令为 `true`；`PRAISE/SCOLD` 为 `false` 并进入情绪链路。Voice 发布成功不等于动作已经完成。 |
| `latency_ms` | number | 事件对应的决策阶段耗时：KWS 选中事件为 VAD 返回后到最终仲裁发布，目录事件为词库查找，Model K 事件为意图解析和路由；身份/状态等无独立决策计时的事件可为 0。KWS 首次候选耗时见 slot `kws_candidate_latency_ms` 或 `stage=kws`。它不代表完整端到端耗时。 |
| `payload` | object | 实际发布到 ROS2 Topic 的完整 schema v2 JSON，是事件字段的权威日志副本。 |

完整 `payload` 还包含 `schema_version`、`header`、`response_text`、`danger_type`、
`danger_angle` 等通用字段。其字段类型和枚举以
[ROS2_CONTRACT.md](ROS2_CONTRACT.md) 为准。

### 4.5 其他记录的专属字段

| `record` | 字段 | 含义和判定方法 |
|---|---|---|
| `interaction_start` | `source/state` | 会话来源和进入的状态；`source` 常见为 `wakeup/service`。 |
| `utterance_complete` | `result/event_type/event_types/published_event_types/selected_source/latency_ms` | KWS 被选中为 `published_kws_selected`；ASR 目录核心双发布为 `published_known_and_specific`；合法 OOS 发布 NEUTRAL 后为 `published`。 |
| `service_complete` | `service/task_id/task_type/task_result/error/latency_ms` | 一次 VoiceTask 回调总耗时和完整返回对象。`result=success/failure`。 |
| `interaction_hold` | `operation/hold_token/reason/lease_sec/idle_timer_reset` | `operation=acquire/renew/release/expire`；不同操作只输出适用字段。 |
| `enrollment_publish` | `topic/speaker_id/payload/latency_ms` | 一次录音注册样本的 embedding、进度处理和发布耗时；最终样本还包含落盘及运行时同步。`result=progress/complete`。 |
| `speaker_api_upload` | `speaker_name/shots/source_duration_ms/speech_duration_ms/segment_count/audio_valid/has_effective_speech/error/latency_ms` | FastAPI 已读完文件后，业务 Handler 内的 WAV 内容解析、VAD、embedding、落盘和运行时同步总耗时；不包含 multipart 解析、网络上传、文件读取、HTTP 响应序列化。当前没有各子步骤的独立耗时。 |
| `speaker_management` | `operation/speaker_name/previous_name/speaker_count/error/latency_ms` | `operation=list/rename/delete`；字段随操作变化。`rename/delete` 成功时同步调用已经完成，但运行时识别结果仍需实际验证；`list` 不触发同步。 |
| `interaction_end` | `reason/state` | 会话结束原因和最终状态；`reason` 常见为 `interaction_timeout/stop_listening`。 |

### 4.6 总耗时的正确计算

`event_publish.latency_ms` 是产生该事件的决策阶段耗时；ASR 目录摘要和具体事件应等于
同句 `command_lexicon.latency_ms`，Model K 大类、白名单具体事件和 KNOWN 摘要应等于
同句 `intent.latency_ms`。
KWS 被选中时，事件的 `latency_ms` 是从 VAD 返回后进入处理到最终仲裁发布的耗时；
首次 KWS 候选耗时看事件 slot `kws_candidate_latency_ms` 或同句 `stage=kws`。
`utterance_complete.latency_ms` 是
**VAD 返回后的处理总耗时**，两者都不是从开始收音到最终结果的完整端到端耗时。
当前真正端到端耗时需要在同一 `utterance_id` 下，用日志行墙钟时间计算：

```text
端到端耗时 = utterance_complete 日志时间 - stage_start(vad_capture) 日志时间
```

若只统计模型/规则阶段，应分别使用同一句的 `stage_complete`：

```text
ASR 处理耗时     = stage_complete(stage=asr).latency_ms
声纹处理耗时    = stage_complete(stage=speaker).latency_ms
目录匹配耗时    = stage_complete(stage=command_lexicon).latency_ms
识别仲裁耗时    = stage_complete(stage=recognition_arbitration).latency_ms
意图处理耗时    = stage_complete(stage=intent).latency_ms
VAD 收音阶段耗时 = stage_complete(stage=vad_capture).latency_ms
KWS 首次命中耗时 = stage_complete(stage=kws).latency_ms
```

所有 `latency_ms` 单位均为毫秒。目录命中的句子没有意图处理耗时，因为模型/规则没有
执行；目录未命中的句子才应出现 `stage=intent`。当前日志没有单独输出模型启动加载
耗时、纯 VAD 推理耗时、RKLLM 与规则各自的分项耗时，也没有上传音频各子步骤的分项
耗时。

示例：

```text
VOICE_TRACE {"record":"stage_complete","stage":"asr","result":"ok","interaction_id":"...","utterance_id":"...","latency_ms":86.42,"language":"zh","text_length":2}
VOICE_TRACE {"record":"event_publish","result":"published","event_type":"EVT_VOICE_COMMAND_SIT","interaction_id":"...","utterance_id":"...","asr_text":"坐下","action":"SIT","control":"DO","should_trigger_behavior_tree":true}
```

## 5. 测试执行步骤

### 5.1 环境预检

```bash
source /opt/ros/humble/setup.bash
source /home/cat/ros2_ws/install/setup.bash
ros2 pkg prefix --share marsdog_voice_interaction
ros2 interface show marsdog_voice_interaction/srv/VoiceTask
```

确认安装前缀来自当前 `/home/cat/ros2_ws`，并确认系统中只有一个语音节点：

```bash
ros2 node list
ros2 topic info -v /perception/audio_event
```

### 5.2 启动节点并保存日志

测试人员应运行已构建、已安装的 ROS2 包。构建和安装命令由发布说明或 `README.md`
维护，本文不重复开发构建流程。每次新开终端先执行：

```bash
source /opt/ros/humble/setup.bash
source /home/cat/ros2_ws/install/setup.bash
VOICE_SHARE="$(ros2 pkg prefix --share marsdog_voice_interaction)"
```

`VOICE_SHARE` 必须指向本次待测版本的安装目录。以下三个命令分别用于不同测试范围，
一次只启动其中一个。

#### 正式链路（真机、硬件和模型验收）

```bash
QA_CASE_DIR=/tmp/marsdog_voice_qa/VOICE-PROD-001
mkdir -p "$QA_CASE_DIR"
ros2 launch marsdog_voice_interaction voice.launch.py \
  config_path:="$VOICE_SHARE/config/voice.yaml" \
  log_level:=INFO \
  log_dir:="$QA_CASE_DIR"
```

#### Event Mock（只验证事件协议和下游消费）

```bash
QA_CASE_DIR=/tmp/marsdog_voice_qa/VOICE-EVENT-MOCK-001
mkdir -p "$QA_CASE_DIR"
ros2 launch marsdog_voice_interaction voice.launch.py \
  config_path:="$VOICE_SHARE/config/voice.mock.yaml" \
  log_level:=INFO \
  log_dir:="$QA_CASE_DIR"
```

#### Pipeline Mock（验证节点编排和阶段耗时）

```bash
QA_CASE_DIR=/tmp/marsdog_voice_qa/VOICE-PIPELINE-MOCK-001
mkdir -p "$QA_CASE_DIR"
ros2 launch marsdog_voice_interaction voice.launch.py \
  config_path:="$VOICE_SHARE/config/voice.pipeline.mock.yaml" \
  log_level:=INFO \
  log_dir:="$QA_CASE_DIR"
```

启动命令会占用当前终端并持续运行；测试结束使用 `Ctrl+C` 正常停止，不要直接关闭
电源或杀死进程。日志仍保存在对应的 `QA_CASE_DIR`。

#### 启动成功判定

另开终端并加载同一 ROS2 环境，然后执行：

```bash
ros2 node list | rg '^/voice_interaction$'
ros2 topic info -v /perception/audio_event
ros2 service type /perception/voice/task
```

同时在本次日志目录中找到唯一一条 `record=runtime_start`，并按模式检查：

| 启动模式 | `runtime_start.runtime_mode` | 必须检查的内容 |
|---|---|---|
| 正式链路 | `production` | 所需 Provider 的 `available=true`；`command_lexicon.ready=true/command_count=81/core_command_count=19/phrase_count=155/expanded_phrase_count=1550/total_match_phrase_count=1705/source_row_count=116/covered_source_row_count=116`；`speaker_api.enabled=true/ready=true` |
| Event Mock | `mock_event` | `mock_event.class=MockEventProvider` 且 `available=true`，`speaker_api.enabled=false` |
| Pipeline Mock | `mock_pipeline` | Wakeup、Audio、ASR、Speaker 为对应的 `Mock*Provider` 且可用；`command_lexicon.ready=true/command_count=81/core_command_count=19/phrase_count=155/expanded_phrase_count=1550/total_match_phrase_count=1705/source_row_count=116/covered_source_row_count=116`；规则意图可用；KWS 禁用 |

模式、配置路径、Provider 或 API 状态不符合预期时，应停止测试并记为环境/启动失败，
不能继续出具功能 PASS。出现多个 `/voice_interaction` 节点时也必须先清理重复进程。

正式链路还要核对 Model K 文件：

```bash
sha256sum /path/to/models/llm/qwen2_5_5b_rk3588_260829_w8a8.rkllm
```

预期 SHA-256 为
`3c316cede8dcc40c6f019f7a2403f56c2d567eeacc29f410b656eb02981ca0b1`。文件名或校验值
任一不符，意图用例记为环境失败。

正式链路还应检查声纹 API；本机执行：

```bash
curl -sS http://127.0.0.1:8091/health
```

预期返回 `{"ok":true,"service":"marsdog-voice-speaker-api"}`。局域网测试机将
`127.0.0.1` 替换为机器狗 IP；Event Mock 和 Pipeline Mock 默认不启动该 API，不能
用 `/health` 失败判定这两种模式启动失败。

### 5.3 另开终端保存接口原文

```bash
source /opt/ros/humble/setup.bash
source /home/cat/ros2_ws/install/setup.bash
ros2 topic echo /perception/audio_event
```

声纹注册用例同时监听：

```bash
ros2 topic echo /perception/voice/enrollment_event
```

声纹文件上传用例直接调用 FastAPI，并保存 HTTP 请求参数、响应 JSON 和同一时段的
`speaker_api_upload` 日志：

```bash
curl -sS -X POST http://127.0.0.1:8091/api/v1/speakers \
  -F 'name=owner' \
  -F 'audio=@/path/to/qa-speaker.wav;type=audio/wav'
```

`name` 只能从 `owner`、`family_member_1`、`family_member_2`、
`family_member_3`、`family_member_4` 中选择，FastAPI `/docs` 应显示枚举选择而不是
自由文本输入。

判定成功时必须同时满足：HTTP `201`、`ok=true`、`speech_duration_ms>0`、返回的
`audio_path/embedding_path` 存在，且落盘 WAV 仅包含 VAD 保留的有效语音。不能仅以
接口收到文件作为声纹注册成功。

声纹接口需要把 HTTP 层和业务层分开解析：

| 证据 | 能证明的内容 | 不能证明的内容 |
|---|---|---|
| Uvicorn access log | 客户端地址、HTTP 方法、路径和最终状态码 | VAD、embedding 和落盘是否正确 |
| HTTP 响应 JSON | 成功时的 `request_id/ok/name/speaker_role/shots/path/duration`，失败时的 `detail` | ROS 运行时声纹索引是否能实际命中 |
| `speaker_api_upload` | Handler 内 WAV 解析、VAD、embedding、落盘及同步调用结果 | multipart/网络上传耗时，以及 FastAPI 在进入 Handler 前拒绝的请求 |
| `speaker_management` | GET/PATCH/DELETE 业务操作结果 | FastAPI 路径或请求体校验阶段直接拒绝的请求 |

以下情况由 FastAPI 在调用声纹业务 Handler 前直接返回，因此通常只有 access log 和
HTTP 响应，**没有** `speaker_api_upload/speaker_management`：

- 缺少 `name` 或 `audio`、`name` 不在固定枚举、PATCH 请求体结构/目标身份不合法：HTTP `422`；
- 空上传文件：HTTP `400`；
- 文件超过配置大小：HTTP `413`；
- 文件名扩展名不是 `.wav`：HTTP `415`；
- 身份路径参数本身不符合接口约束：HTTP `422`。

进入业务 Handler 后发生的 WAV 内容损坏、VAD 无语音、声纹提取失败等，才会同时
看到 `speaker_api_upload result=failure`。FastAPI 的失败响应采用
`{"detail":"错误原因"}`，不是成功响应中的 `ok/error` 结构。当前 HTTP 成功响应的
`request_id` 没有写入 `VOICE_TRACE`，只能使用请求时间、客户端地址、人员名称和
Uvicorn access log 关联；不得声称已经通过 `request_id` 完成日志关联。

同时执行以下异常和管理用例：

- 上传非 WAV、截断 WAV、无有效语音和有效段不足 0.5 秒的文件，均应返回 `4xx`，
  且不产生人员目录。
- 请求中附带 `storage_root/path/output_dir` 等字段，必须不能改变配置中的落盘目录。
- 分别建立 `owner` 和 4 个 `family_member_*`，确认恰好 5 个固定身份槽位；提交任意
  自定义姓名或 `family_member_5` 返回 HTTP `422`，且不调用业务 Handler、不落盘。
- 给已有身份追加样本仍成功；PATCH 只能改到另一个未占用的固定身份槽位，目标已
  占用返回 HTTP `409`。
- 对同一人员连续上传 5 个有效样本后，第 6 个返回 HTTP `409`，目录内不得出现
  `006.wav/006.npy`；`GET` 返回 `max_samples_per_speaker=5`。
- `GET` 返回 `count=5/max_speakers=5`、完整 `allowed_names`、空的
  `available_names`，且每项 `role` 正确；`PATCH` 变更身份后目录和检索名称同步；
  `DELETE` 后身份目录及运行时索引均移除，随后可以重新注册该空闲槽位。

声纹识别事件按下表判定；三类事件都在 `/perception/audio_event` 上发布，由行为树等
下游消费：

| 测试声纹 | `speaker_id` | 期望 `event_type` |
|---|---|---|
| 主人 | `owner` | `EVT_VOICE_MASTER_ID` |
| 任一家人 | `family_member_1`～`family_member_4` | `EVT_VOICE_FOLK_ID` |
| 未注册/未匹配人员 | `unknown` | `EVT_VOICE_UNMASTER_ID` |
| 历史自由名称（兼容数据） | 原历史名称 | `EVT_VOICE_UNMASTER_ID` |

新运行时不得再发布 `EVT_VOICE_STRANGER_ID`。仅看到
`stage_complete stage=speaker result=matched` 还不够，必须检查同一
`utterance_id` 的 `event_publish.event_type` 和 Topic 原文。

需要跨项目复现或时序分析时，额外录制 rosbag：

```bash
ros2 bag record /perception/audio_event /perception/voice/enrollment_event
```

### 5.4 提取测试追踪

```bash
rg 'VOICE_TRACE' /tmp/marsdog_voice_qa/VOICE-MOCK-001
rg '"record":"event_publish"' /tmp/marsdog_voice_qa/VOICE-MOCK-001
rg '"record":"stage_complete"' /tmp/marsdog_voice_qa/VOICE-MOCK-001
rg '\[ERROR\]|\[WARNING\]' /tmp/marsdog_voice_qa/VOICE-MOCK-001
```

## 6. 功能判定清单

| 功能 | 必须看到的结果 | 必须关联/检查的耗时 |
|---|---|---|
| 节点启动 | `runtime_start result=ready`，Topic/Service 正确 | 各 Provider `available=true` |
| 唤醒 | `interaction_start` 后发布 `EVT_VOICE_CALL_NAME` | 唤醒事件的角度、置信度；硬件响应时间由外部操作时间对照 |
| VAD | `stage_complete stage=vad_capture result=voice` | `latency_ms`、`audio_duration_ms` |
| KWS 候选 | 命中时只缓存、不发布业务事件；最终选中 KWS 后才发布结果组 | `stage_complete stage=kws result=candidate latency_ms/candidate_count` |
| ASR | 发布 `speech`，`asr_text` 与实说内容对照 | `stage_complete stage=asr latency_ms` |
| 声纹识别 | `owner` 发布 MASTER，`family_member_*` 发布 FOLK，未匹配/历史名称发布 UNMASTER | `stage_complete stage=speaker latency_ms/speaker_id/speaker_confidence`；当前 confidence 仅为匹配指示值 |
| 完整确定性词库 | 核心项发布不可执行 KNOWN 摘要及目录具体事件，其他项发布目录事件；该句不执行 Intent | `command_lexicon matched/latency_ms`，核心另查两个 `dispatch_role` 和 `specific_event_type` |
| 目录外意图 | 三轴及事件顺序符合契约；仅白名单具体动作可执行 | `command_lexicon no_match` 后检查 `stage=intent social/intent/control/event_types/latency_ms`；找物类还要检查 `stage=object_target` |
| KWS/ASR 仲裁 | 短指令可选 KWS；例如“击掌”被 ASR 转写为“机长”但唯一 HIGH_FIVE 候选胜出时，ASR 单项失败、命令路由通过；长句、多个 KWS 候选或目录冲突选择 ASR；只发布一个来源的业务结果 | `stage_complete stage=recognition_arbitration result/selected_source/reason/kws_candidate_count` |
| 静默结束 | 发布匹配 ID 的 `EVT_STATE_CHANGED state=idle` | `interaction_end reason=interaction_timeout`，约 10 秒 |
| 手动监听 | VoiceTask 返回成功并带当前 ID | `service_complete latency_ms/task_result` |
| 会话保持 | hold 后不超时；release/租约到期后恢复超时 | Service 结果、`interaction_hold`、结束时间 |
| 声纹注册 | 注册 Topic 连续进度，最终 `done=true` | `enrollment_publish result=complete/latency_ms` |
| 声纹 API 上传 | `runtime_start.speaker_api.ready=true`，HTTP 201，`audio_valid/has_effective_speech=true`，VAD 后 WAV/embedding/centroid 均落盘 | `speaker_api_upload result=success` 及源音频/有效语音/总耗时 |
| 声纹身份限制与管理 | 固定 5 个身份槽位；自由名称返回 422；列表、身份变更、删除与目录及运行时索引一致 | HTTP 状态码和 `speaker_management operation/result/latency_ms` |
| 单人样本限制 | 每人最多 5 个；第 6 次同名上传返回 409 且无 `006` 文件 | HTTP 状态码、列表 `shots/max_samples_per_speaker` 和目录文件数 |

判定时遵守以下规则：

- 预期事件没有出现在 `event_publish` 和 Topic 原文中，就是 Voice 未发布；不要用
  Provider 的“detected/matched”普通日志代替发布结果。
- `event_publish` 已出现但动作未执行，继续查行为树和动作项目，不判 Voice 失败。
- `stage_complete result=error/empty`、意外 Mock Provider 或任意未解释的 ERROR，
  该用例不能判 PASS。
- 性能门限由测试计划或产品指标给出。尚未给定门限时只记录原始值、P50/P95 和
  样本量，不临时发明合格线。

## 7. 测试报告模板

每条用例使用下面的最小结构：

```text
用例 ID：VOICE-ASR-001
代码版本：<git commit 或明确写 working tree>
日期/设备/环境：
运行模式和配置：production / config/voice.yaml
实际 Provider：<复制 runtime_start.providers>
词库版本与统计：<复制 runtime_start.command_lexicon>
源数据行 / 路由组 / 标准词句 / 扩展 / 总入口：116/116 / 81/81 / 155/155 / 1550/1550 / 1705
目标指令数 / 已实现数 / 缺失数：
功能覆盖率：
实际执行次数 / 成功次数 / 识别准确率：
输入与步骤：说“坐下”3 次，距离 1 m，环境噪声 xx dB
预期：每次 speech=坐下；最终 EVT_VOICE_COMMAND_SIT；不重复发布
实际：
关联 interaction_id：
关联 utterance_id：
关键事件时间线：
阶段耗时：VAD / KWS / ASR / Speaker / Command Lexicon / Intent / VAD 后处理总耗时 / 计算得到的端到端耗时
路由结果：command_lexicon matched/no_match / command_key / event_type / match_strategy / catalog_phrase / matched_phrase / expansion_profile / expansion_rule / source_rows / intent_source
结果：PASS / FAIL / N/A-MISSING_ACCEPTED / BLOCKED-MANIFEST / KNOWN-GAP
最早异常时间和错误：
附件：节点日志、Topic 原文、Service 返回、rosbag、必要时视频
```

批量性能报告至少给出样本量、成功率、P50、P95、最大值，并区分
`vad_capture`、`kws`、`asr`、`speaker`、`command_lexicon`、`intent`、VAD 后处理
总耗时和计算得到的端到端耗时，禁止把不同定义的 `latency_ms` 混在同一列。

## 8. 交付给测试团队的文件

建议每个测试版本提供：

1. 本文档 `docs/TESTING_LOG_GUIDE.md`：测试执行和日志判定。
2. `docs/COMMAND_CATALOG_TEST_MATRIX.md`：155 条标准中文词/句、扩展规则与期望事件对齐表。
3. `docs/ROS2_CONTRACT.md`：事件、字段、枚举和 Service 权威契约。
4. `docs/HANDOFF.md`：上下游职责和跨项目语义。
5. 三份运行配置和确定性目录：`voice.yaml`、`voice.mock.yaml`、
   `voice.pipeline.mock.yaml`、`command_catalog.yaml`。
6. 发布说明：Git commit、构建时间、模型版本/校验值、已知限制和本轮变更。
7. 一份已跑通的示例证据包，证明测试命令和日志提取方式可复现。

语音文本、说话人 ID、声纹样本和注册表属于本地测试/生物特征数据。日志和证据包
按内部敏感数据管理，不提交公开仓库；`data/speakers`、注册表和原始音频不得随代码
交付。
