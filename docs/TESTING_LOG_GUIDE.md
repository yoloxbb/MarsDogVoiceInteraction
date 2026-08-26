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
`emotion/action/control/event_type/command_id/slots`。测试表中的
`{"action":"fetch","target":"ball"}` 仅是示意，不要求程序输出该结构；物体
指令当前使用 `action=BRING/FETCH`，物体名称放在
`slots=[{"key":"object_name","value":"..."}]`。普通动作不要求目标字段。

### 当前能力基线

代码和配置中的可验证库存为：

- 完整确定性词库：`config/command_catalog.yaml` 覆盖产品表 **116 条源数据**
  （不含表头），归并为 **81 个路由组、155 条可运行中文短语**。ASR 文本
  整句精确命中后直接发布目录指定的 `EVT_VOICE_*`，不经过意图模型。测试人员按
  [COMMAND_CATALOG_TEST_MATRIX.md](COMMAND_CATALOG_TEST_MATRIX.md) 逐条对齐短语和事件。
- 116 条源数据中 72 条已明确 `ACT_*`，目录保留原始动作名和“具体行为”全文；
  其余行按呼名、夸赞、责备或同类生理/娱乐语义归并，不伪造未定义的 `ACT_*`。
- 19 组核心训练指令是完整词库的子集。当前行为树已有 11 个核心动作映射；
  `EVT_VOICE_CALL_NAME` 已有路由，`PRAISE/SCOLD` 进入情绪链路。按当前源码可确认
  81 个路由组中 14 个有现成下游入口，其余 67 个仍需 Tree/Action 补齐映射。
- 旧版意图协议仍保留 16 类可执行动作映射；它与完整确定性词库是两套用途，
  不能用旧版 16 类数量替代词库覆盖率。
- 流式 KWS 配置有 13 条中文和 13 条英文，共 26 条关键词，当前对应 12 个不同动作
  标签。其中 11 个与核心目录重合，`WAIT` 是旧版 KWS 指令；“回来/COME BACK”已
  统一映射为 `COME`，不再映射 `RETURN`。
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
| 1 | 以 `command_catalog.yaml` 中 `core=true` 的 19 组为核心清单，每组代表短语播放 20 次，并补测全部别名。 | 同一句依次出现 `stage_complete stage=asr`、`stage_complete stage=command_lexicon result=matched` 和预期 `event_publish`；检查 `command_id/event_type/intent_source=command_lexicon` 及关联 ID。 | 每组代表短语至少 17/20，且 19/19 均有结果。只有现成 Tree/Action 映射的组可判端到端 PASS；其余组可判 Voice 发布 PASS，下游动作列记 `KNOWN-GAP`。 |
| 2 | 以产品表 116 条数据和目录展开后的 155 条中文短语执行全量覆盖测试。不再要求测试方另外提供“117 组”清单。 | 记录 `command_lexicon` 的 `matched/command_key/event_type/action_name/source_rows`，并复核事件 slots 的 `catalog_source_rows`；只有未命中时才记录 `intent_source=rule/rkllm/fallback`。 | 每条测试 10 次时，85% 门限必须取整为至少 **9/10**。同时报告源数据覆盖 `116/116`、路由组 `81/81` 和短语 `155/155`；下游未映射项不得判端到端 PASS。 |
| 3 | 使用唤醒词启动正式会话，并分别播放一条目录内指令和一条目录外语义文本。 | 两条都应有 ASR；目录内指令随后 `command_lexicon result=matched` 且不出现该句 `stage=intent`；目录外文本 `result=no_match` 后才出现 `stage=intent`。 | 同一 `interaction_id` 内两条链路各自完整且无 ERROR。仅有唤醒事件不能证明后续模块已工作。 |
| 4 | 机播已知文本，对照 `speech` 事件中的 `asr_text` 和完整 `payload`。 | `stage_complete stage=asr result=ok`；`event_publish event_type=speech`；完整 ROS2 原文。 | `asr_text` 与期望文本一致或符合用例允许的等价转写；JSON 字段符合当前 ROS2 契约。测试表里的 `action/target` 不作为格式标准。 |
| 5 | 对编号 4 的同一 `utterance_id` 检查最终路由结果。 | 目录命中看 `stage=command_lexicon` 和最终 payload；目录未命中才看 `stage=intent`。 | 目录命中必须得到指定 `command_id/event_type`。可执行指令要求 `control=DO/should_trigger_behavior_tree=true`；`PRAISE/SCOLD` 要求 `control=NONE/should_trigger_behavior_tree=false` 并进入情绪链路。目录未命中才评估意图模型结果。 |
| 6 | 对已有中英文 KWS 逐条执行，并对完整中文 ASR 词库逐条执行；两类覆盖率分开报告。 | KWS 看 `intent_source=kws`；目录看 `intent_source=command_lexicon`；其余英文表达看最终意图来源。 | KWS 按 26 条配置逐条验收，不将“26 条关键词”误写成 26 组；目录按 155 条中文短语验收。138 条英文参考项当前不做确定性直发验收，需产品确认唯一归属后再启用。 |
| 7 | 在一个会话内连续播放 3 条指令，每条之间保留正常句尾静音；既测试三条不同指令，也测试同一指令连续 3 次。 | 一个 `interaction_id` 下出现 3 个不同 `utterance_id`；逐句检查 `utterance_complete`、目录匹配和最终事件。 | 三句均得到正确结果。同一句 KWS 与目录事件相同只发布一次；不同时只保留先发 KWS 并出现 `command_conflict result=suppressed`，禁止一次话触发两个动作。 |
| 8 | 使用相似音和在短语前后增加其他词的句子探索拒识，例如“官过来”“你要不要过来”。 | 记录 KWS、ASR、`command_lexicon matched/no_match`、意图阶段和任何可执行事件。 | 目录必须是规范化后的整句精确匹配，不能因子串命中；流式 KWS 的相似音拒识仍属 `KNOWN-GAP`，单独记录，不能用目录结果掩盖 KWS 误触发。 |
| 9 | 播放陌生词，并在最后一次有效 ASR 文本后等待至少 10 秒。 | 应先看到 `command_lexicon result=no_match`，再由意图链路产生 UNKNOWN；最后出现同会话 `EVT_STATE_CHANGED state=idle state_reason=interaction_timeout` 和 `interaction_end`。 | 不发布可执行动作、不崩溃、不持续占用会话，约 10 秒后恢复待机。若模型实际推理出合法结果，应按模型结果另行记录，不能伪造 UNKNOWN。 |

### 测试常用固定日志关键字

测试表中要求的“SDK 日志关键字”统一使用下列稳定字段，不依赖第三方 SDK 的临时
中文描述：

```text
VOICE_TRACE {"record":"runtime_start"...}
VOICE_TRACE {"record":"interaction_start"...}
VOICE_TRACE {"record":"stage_start","stage":"vad_capture"...}
VOICE_TRACE {"record":"stage_complete","stage":"asr"...}
VOICE_TRACE {"record":"stage_complete","stage":"command_lexicon"...}
VOICE_TRACE {"record":"stage_complete","stage":"intent"...}
VOICE_TRACE {"record":"command_conflict"...}
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
没有发布错误的可执行动作。目录命中后不应再出现该句 `stage=intent`。只有
`speech.asr_text` 正确但目录未命中或事件错误，仍记为失败；KWS 已先发布相同事件且
目录结果被 `suppressed_duplicate` 时记为成功。各组必须分别达到门限，不能用总体
平均准确率掩盖某一组不达标。目录外文本才使用 `emotion/action/control` 意图判定表。

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
| `runtime_start` | 节点就绪 | `runtime_mode/providers/command_lexicon/speaker_api/config_path/log_file` | 确认模式、配置、指令目录、真实 Provider 和 API 状态 |
| `interaction_start` | 会话开始 | `source/interaction_id/state` | 确认唤醒或 Service 建立会话 |
| `stage_start` | 开始收音 | `stage/interaction_id/utterance_id` | 确认一句话的计时起点 |
| `stage_complete` | 阶段结束 | `stage/result/latency_ms` | VAD、KWS、ASR、声纹、确定性目录、意图耗时与结果 |
| `command_conflict` | KWS 与目录结果冲突 | `immediate_event_types/catalog_event_type/result` | 证明后发冲突事件已抑制，没有一语双动作 |
| `event_publish` | 发布音频事件 | `event_type/interaction_id/utterance_id/state/payload` | 用完整 payload 对照 Topic 原文和下游入口 |
| `utterance_complete` | 整句处理结束 | `result/event_type/latency_ms` | 判断最终路由、发布、去重或冲突抑制 |
| `service_complete` | VoiceTask 返回 | `task_id/task_type/result/latency_ms/task_result` | Service 成败与耗时 |
| `interaction_hold` | 租约申请、续租、释放或到期 | `operation/result/hold_token/reason` | 验证保持租约生命周期 |
| `enrollment_publish` | 发布注册进度 | `result/speaker_id/latency_ms` | 声纹注册阶段结果 |
| `speaker_api_upload` | 上传文件进入声纹业务处理后结束 | `result/speaker_name/audio_valid/has_effective_speech/source_duration_ms/speech_duration_ms/segment_count/latency_ms` | 判断 WAV 内容解析、VAD 截取、声纹提取和落盘结果；不代表完整 HTTP 请求耗时 |
| `speaker_management` | 查询、改名或删除 | `operation/result/speaker_name/speaker_count/latency_ms` | 复核声纹管理和运行时索引同步 |
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
| `command_lexicon` | object | 词库实际加载状态和统计。正式与 Pipeline Mock 应为 `ready=true/command_count=81/core_command_count=19/phrase_count=155/reference_phrase_count=138/source_row_count=116/covered_source_row_count=116`；`reference_phrase_count` 只是元数据数量，不代表英文已启用匹配。 |
| `speaker_api` | object | `enabled/ready/address/docs`；启动失败时包含 `error`。 |

### 4.3 `stage_complete` 字段和耗时边界

| `stage` | `result` 可能值 | 阶段专属字段 | `latency_ms` 的准确范围 |
|---|---|---|---|
| `vad_capture` | `voice/silence` | `audio_duration_ms` | 从分配本句并启动收音，到 VAD 结果被节点取出；包含等待说话、有效语音、句尾静音和线程轮询，不是纯 VAD 模型推理耗时。 |
| `kws` | `detected` | `event_type` | 从本句开始收音到 KWS 首次命中并取出事件；不是单个 KWS 模型调用耗时。未命中时没有该记录。 |
| `asr` | `ok/empty/error` | `language/text_length` | 一次 `transcribe()` 调用的总耗时。`ok` 仅表示得到非空文本，不表示文本一定正确。 |
| `speaker` | `matched/unknown/error` | `speaker_id/speaker_confidence` | 声纹 embedding 提取、已注册人员检索和阈值判定的总耗时。 |
| `command_lexicon` | `matched/no_match/unavailable` | `command_key/event_type/catalog_version/emotion/control/action_name/source_rows/core` | 一次规范化及精确目录查找的总耗时。`source_rows` 是产品源表数据行号（不含表头）；`matched` 后跳过意图模型，`no_match` 后才进入 `intent`。 |
| `intent` | `parsed/fallback_unknown` | `event_type/intent_source` | `_parse_intent()` 总耗时；可能只包含 RKLLM，也可能包含 RKLLM 未返回结果后再执行规则的累计时间。 |

补充判定规则：

- `audio_duration_ms` 是交给 ASR/声纹的结果音频长度，不是 VAD 阶段耗时。
- `intent_source` 常见值为 `command_lexicon/kws/rkllm/rule/fallback`。其中
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
| `speaker_id` / `speaker_confidence` | string / number | 已知人员名称或 `unknown`，以及当前实现的匹配指示值。 |
| `emotion` / `action` / `control` | string | 兼容字段。可执行目录指令写入 `NONE/command_key/DO`；`PRAISE/SCOLD` 写入 `PRAISE|NONE|NONE` 或 `REPRIMAND|NONE|NONE`。目录验收主键仍是 `command_id/event_type`。 |
| `command_id` / `intent_category` / `intent_source` | string | 命令标识、分类类别及决策来源；目录事件要求 `command_id` 等于目录声明值、`intent_source=command_lexicon`。 |
| `intent_confidence` | number | 意图 Provider 给出的置信度；不能与声纹或唤醒置信度混用。 |
| `slots` | array | `[{"key":"...","value":"..."}]`；物体名使用 `object_name`。 |
| `is_executable` | bool | 当前事件是否表示可执行意图；完整值在 `payload` 中。 |
| `should_trigger_behavior_tree` | bool | 可执行指令为 `true`；`PRAISE/SCOLD` 为 `false` 并进入情绪链路。Voice 发布成功不等于动作已经完成。 |
| `latency_ms` | number | 事件业务对象自身携带的延迟字段，不代表整句处理耗时。 |
| `payload` | object | 实际发布到 ROS2 Topic 的完整 v1 JSON，是事件字段的权威日志副本。 |

完整 `payload` 还包含 `schema_version`、`header`、`response_text`、`danger_type`、
`danger_angle` 等通用字段。其字段类型和枚举以
[ROS2_CONTRACT.md](ROS2_CONTRACT.md) 为准。

### 4.5 其他记录的专属字段

| `record` | 字段 | 含义和判定方法 |
|---|---|---|
| `interaction_start` | `source/state` | 会话来源和进入的状态；`source` 常见为 `wakeup/service`。 |
| `utterance_complete` | `result/event_type/latency_ms` | 目录可执行指令为 `published_direct_command`，非执行社交事件为 `published_catalog_event`，去重/冲突为 `suppressed_duplicate/suppressed_conflict`；意图路径常见 `published/empty_asr`。耗时从 VAD 返回后开始，包含 ASR、声纹、路由和事件处理，但不包含收音/VAD。 |
| `command_conflict` | `immediate_event_types/catalog_event_type/result` | 同一句 KWS 与目录结果不一致时输出；当前 `result=suppressed` 表示保留先发 KWS、没有再发布冲突目录事件。 |
| `service_complete` | `service/task_id/task_type/task_result/error/latency_ms` | 一次 VoiceTask 回调总耗时和完整返回对象。`result=success/failure`。 |
| `interaction_hold` | `operation/hold_token/reason/lease_sec/idle_timer_reset` | `operation=acquire/renew/release/expire`；不同操作只输出适用字段。 |
| `enrollment_publish` | `topic/speaker_id/payload/latency_ms` | 一次录音注册样本的 embedding、进度处理和发布耗时；最终样本还包含落盘及运行时同步。`result=progress/complete`。 |
| `speaker_api_upload` | `speaker_name/shots/source_duration_ms/speech_duration_ms/segment_count/audio_valid/has_effective_speech/error/latency_ms` | FastAPI 已读完文件后，业务 Handler 内的 WAV 内容解析、VAD、embedding、落盘和运行时同步总耗时；不包含 multipart 解析、网络上传、文件读取、HTTP 响应序列化。当前没有各子步骤的独立耗时。 |
| `speaker_management` | `operation/speaker_name/previous_name/speaker_count/error/latency_ms` | `operation=list/rename/delete`；字段随操作变化。`rename/delete` 成功时同步调用已经完成，但运行时识别结果仍需实际验证；`list` 不触发同步。 |
| `interaction_end` | `reason/state` | 会话结束原因和最终状态；`reason` 常见为 `interaction_timeout/stop_listening`。 |

### 4.6 总耗时的正确计算

`event_publish.latency_ms` 是事件自身携带的延迟；`utterance_complete.latency_ms` 是
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
| 正式链路 | `production` | 所需 Provider 的 `available=true`；`command_lexicon.ready=true/command_count=81/core_command_count=19/phrase_count=155/source_row_count=116/covered_source_row_count=116`；`speaker_api.enabled=true/ready=true` |
| Event Mock | `mock_event` | `mock_event.class=MockEventProvider` 且 `available=true`，`speaker_api.enabled=false` |
| Pipeline Mock | `mock_pipeline` | Wakeup、Audio、ASR、Speaker 为对应的 `Mock*Provider` 且可用；`command_lexicon.ready=true/command_count=81/core_command_count=19/phrase_count=155/source_row_count=116/covered_source_row_count=116`；规则意图可用；KWS 禁用 |

模式、配置路径、Provider 或 API 状态不符合预期时，应停止测试并记为环境/启动失败，
不能继续出具功能 PASS。出现多个 `/voice_interaction` 节点时也必须先清理重复进程。

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
  -F 'name=QA测试员' \
  -F 'audio=@/path/to/qa-speaker.wav;type=audio/wav'
```

判定成功时必须同时满足：HTTP `201`、`ok=true`、`speech_duration_ms>0`、返回的
`audio_path/embedding_path` 存在，且落盘 WAV 仅包含 VAD 保留的有效语音。不能仅以
接口收到文件作为声纹注册成功。

声纹接口需要把 HTTP 层和业务层分开解析：

| 证据 | 能证明的内容 | 不能证明的内容 |
|---|---|---|
| Uvicorn access log | 客户端地址、HTTP 方法、路径和最终状态码 | VAD、embedding 和落盘是否正确 |
| HTTP 响应 JSON | 成功时的 `request_id/ok/name/shots/path/duration`，失败时的 `detail` | ROS 运行时声纹索引是否能实际命中 |
| `speaker_api_upload` | Handler 内 WAV 解析、VAD、embedding、落盘及同步调用结果 | multipart/网络上传耗时，以及 FastAPI 在进入 Handler 前拒绝的请求 |
| `speaker_management` | GET/PATCH/DELETE 业务操作结果 | FastAPI 路径或请求体校验阶段直接拒绝的请求 |

以下情况由 FastAPI 在调用声纹业务 Handler 前直接返回，因此通常只有 access log 和
HTTP 响应，**没有** `speaker_api_upload/speaker_management`：

- 缺少 `name` 或 `audio`、字段长度不合法、PATCH 请求体结构不合法：HTTP `422`；
- 空上传文件：HTTP `400`；
- 文件超过配置大小：HTTP `413`；
- 文件名扩展名不是 `.wav`：HTTP `415`；
- 人员路径参数本身不符合接口约束：HTTP `422`。

进入业务 Handler 后发生的 WAV 内容损坏、VAD 无语音、声纹提取失败等，才会同时
看到 `speaker_api_upload result=failure`。FastAPI 的失败响应采用
`{"detail":"错误原因"}`，不是成功响应中的 `ok/error` 结构。当前 HTTP 成功响应的
`request_id` 没有写入 `VOICE_TRACE`，只能使用请求时间、客户端地址、人员名称和
Uvicorn access log 关联；不得声称已经通过 `request_id` 完成日志关联。

同时执行以下异常和管理用例：

- 上传非 WAV、截断 WAV、无有效语音和有效段不足 0.5 秒的文件，均应返回 `4xx`，
  且不产生人员目录。
- 请求中附带 `storage_root/path/output_dir` 等字段，必须不能改变配置中的落盘目录。
- 连续建立 5 个不同人员后，第 6 人返回 HTTP `409`；给已有人员追加样本仍成功。
- 对同一人员连续上传 5 个有效样本后，第 6 个返回 HTTP `409`，目录内不得出现
  `006.wav/006.npy`；`GET` 返回 `max_samples_per_speaker=5`。
- `GET` 返回 `count=5/max_speakers=5`；`PATCH` 改名后目录和检索名称同步变化；
  `DELETE` 后人员目录及运行时索引均移除，随后可以再新增一人。

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
| KWS | 发布对应 `EVT_VOICE_COMMAND_*`，来源为 KWS | `stage_complete stage=kws latency_ms` |
| ASR | 发布 `speech`，`asr_text` 与实说内容对照 | `stage_complete stage=asr latency_ms` |
| 声纹识别 | 发布 MASTER 或 STRANGER 身份事件 | `stage_complete stage=speaker latency_ms/speaker_confidence`；当前 confidence 仅为匹配指示值 |
| 完整确定性词库 | 发布目录指定的 `command_id/event_type`，`intent_source=command_lexicon`，且该句不执行 Intent；覆盖 116 条源数据/81 个路由组/155 条中文短语 | `stage_complete stage=command_lexicon result=matched/latency_ms/action_name/source_rows`，并检查事件 slots 中的 `action_name/catalog_source_rows` |
| 目录外意图 | 事件的 `emotion/action/control/event_type` 符合契约 | 先有 `command_lexicon result=no_match`，再检查 `stage_complete stage=intent latency_ms/intent_source` |
| KWS/目录去重 | 相同事件不再发布；不同事件抑制后发冲突事件 | `utterance_complete result=suppressed_duplicate/suppressed_conflict`，冲突时另有 `command_conflict` |
| 静默结束 | 发布匹配 ID 的 `EVT_STATE_CHANGED state=idle` | `interaction_end reason=interaction_timeout`，约 10 秒 |
| 手动监听 | VoiceTask 返回成功并带当前 ID | `service_complete latency_ms/task_result` |
| 会话保持 | hold 后不超时；release/租约到期后恢复超时 | Service 结果、`interaction_hold`、结束时间 |
| 声纹注册 | 注册 Topic 连续进度，最终 `done=true` | `enrollment_publish result=complete/latency_ms` |
| 声纹 API 上传 | `runtime_start.speaker_api.ready=true`，HTTP 201，`audio_valid/has_effective_speech=true`，VAD 后 WAV/embedding/centroid 均落盘 | `speaker_api_upload result=success` 及源音频/有效语音/总耗时 |
| 声纹人数限制与管理 | 最多 5 人；列表、改名、删除结果与目录及运行时索引一致 | HTTP 状态码和 `speaker_management operation/result/latency_ms` |
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
源数据行 / 路由组 / 中文短语覆盖：116/116 / 81/81 / 155/155
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
路由结果：command_lexicon matched/no_match / command_key / event_type / action_name / source_rows / intent_source
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
2. `docs/COMMAND_CATALOG_TEST_MATRIX.md`：155 条中文词/句与期望事件逐条对齐表。
3. `docs/ROS2_CONTRACT.md`：事件、字段、枚举和 Service 权威契约。
4. `docs/HANDOFF.md`：上下游职责和跨项目语义。
5. 三份运行配置和确定性目录：`voice.yaml`、`voice.mock.yaml`、
   `voice.pipeline.mock.yaml`、`command_catalog.yaml`。
6. 发布说明：Git commit、构建时间、模型版本/校验值、已知限制和本轮变更。
7. 一份已跑通的示例证据包，证明测试命令和日志提取方式可复现。

语音文本、说话人 ID、声纹样本和注册表属于本地测试/生物特征数据。日志和证据包
按内部敏感数据管理，不提交公开仓库；`data/speakers`、注册表和原始音频不得随代码
交付。
