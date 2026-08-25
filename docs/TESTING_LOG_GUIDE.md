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

- 可执行动作事件：16 类，分别为 `COME`、`SHAKE_HAND`、`HIGH_FIVE`、`SIT`、
  `LIE_DOWN`、`STAND_UP`、`WAIT`、`FOLLOW`、`ROLL_OVER`、`SPIN`、`RETURN`、
  `DROP`、`PLAY_DEAD`、`BRING`、`FETCH`、`STOP`。
- 流式 KWS 快速识别：13 类动作，配置中有 13 条中文和 13 条英文，共 26 条关键词；
  当前不包含 `BRING/FETCH/STOP` 的 KWS 关键词，这三类仍可能通过 ASR 加意图识别。
- 本地规则意图：30 条正则规则。规则数量不等同于自然语言词条数量。
- 仓库中没有独立的“17 组核心指令清单”或“117 组本地指令清单/训练完成报告”，
  因此不能从代码直接声明 17/117 已全部学习训练完成。

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
| 1 | 先取得测试方的 17 条目标清单，与当前 16 类动作映射逐项比对。已实现项每项播放 20 次；缺失项标记 `N/A-MISSING_ACCEPTED`。 | `event_publish.payload` 中的 `asr_text`、`emotion`、`action`、`control`、`event_type`，以及同一句的 `interaction_id/utterance_id`。 | 已实现的每条指令至少 17/20；同时报告 `已实现数/17`。命令类的 `emotion=NONE` 是合法标签，不要求每条命令产生非空情绪。 |
| 2 | 测试方必须提供 117 条逐条词表及期望 `emotion/action/control`。当前代码不能证明 117 条已训练完成；先做清单映射，再只执行已实现子集。 | 与编号 1 相同，并记录 `intent_source` 是 `kws/rule/rkllm/fallback`。 | 每条测试 10 次时，85% 门限必须取整为至少 **9/10**，不能写 8.5 次；另报 `已实现数/117`。无 117 清单时记 `BLOCKED-MANIFEST`。 |
| 3 | 使用唤醒词启动一次正式会话，并播放一条已实现指令。 | `runtime_start` 中实际 ASR/Intent Provider 可用；随后依次出现 `EVT_VOICE_CALL_NAME`、`stage_start stage=vad_capture`、`stage_complete stage=asr`、`stage_complete stage=intent`。 | 同一 `interaction_id` 内链路完整且无 ERROR。仅有唤醒事件不能证明 ASR 和语义已经完成。 |
| 4 | 机播已知文本，对照 `speech` 事件中的 `asr_text` 和完整 `payload`。 | `stage_complete stage=asr result=ok`；`event_publish event_type=speech`；完整 ROS2 原文。 | `asr_text` 与期望文本一致或符合用例允许的等价转写；JSON 字段符合当前 ROS2 契约。测试表里的 `action/target` 不作为格式标准。 |
| 5 | 对编号 4 的同一 `utterance_id` 继续检查最终意图事件。 | `stage_complete stage=intent`、最终 `event_publish.payload`、`utterance_complete`。 | `emotion/action/control/event_type/command_id/slots` 与该用例期望一致；可执行命令要求 `should_trigger_behavior_tree=true`。物体名按 `slots.object_name` 判断。 |
| 6 | 对测试清单中的中文和英文表达分别执行；区分 KWS 快速路径和 ASR+Intent 路径。 | `intent_source=kws` 或最终意图来源、`language`、`asr_text`、最终 `event_type`。 | 当前 KWS 可直接验收 13 组中英动作。清单中未覆盖的中/英文指令记 `N/A-MISSING_ACCEPTED`，不宣称全量双语已完成。 |
| 7 | 在一个会话内连续播放 3 条指令，每条之间保留正常句尾静音；既测试三条不同指令，也测试同一指令连续 3 次。 | 一个 `interaction_id` 下出现 3 个不同 `utterance_id`；逐句检查 `utterance_complete` 和最终事件。 | 三句均得到正确结果。KWS 与最终意图相同时，同一句只允许一个动作事件；这属于去重成功，不是漏识别。 |
| 8 | 相似音拒识当前未实现，只做探索测试并保存误触发样本。 | 记录输入音频/文本、`stage_complete stage=kws/asr/intent` 和任何错误动作 `event_publish`。 | 标记 `KNOWN-GAP`，不作为本轮阻断项；禁止把当前可能识别为正确指令的结果写成 PASS。后续实现拒识后再启用“10 次零错误动作”门限。 |
| 9 | 播放陌生词，并在最后一次有效 ASR 文本后等待至少 10 秒。 | 无法匹配时应看到 `EVT_VOICE_COMMAND_UNKNOWN`、`should_trigger_behavior_tree=false`，最后出现同会话 `EVT_STATE_CHANGED state=idle state_reason=interaction_timeout` 和 `interaction_end`。 | 不发布可执行动作、不崩溃、不持续占用会话，约 10 秒后恢复待机。若模型实际推理出合法结果，应按模型结果另行记录，不能伪造 UNKNOWN。 |

### 测试常用固定日志关键字

测试表中要求的“SDK 日志关键字”统一使用下列稳定字段，不依赖第三方 SDK 的临时
中文描述：

```text
VOICE_TRACE {"record":"runtime_start"...}
VOICE_TRACE {"record":"interaction_start"...}
VOICE_TRACE {"record":"stage_start","stage":"vad_capture"...}
VOICE_TRACE {"record":"stage_complete","stage":"asr"...}
VOICE_TRACE {"record":"stage_complete","stage":"intent"...}
VOICE_TRACE {"record":"event_publish"...}
VOICE_TRACE {"record":"utterance_complete"...}
VOICE_TRACE {"record":"interaction_end"...}
```

每个 `event_publish` 都带完整的 `payload`，可直接从日志复核 ROS2 JSON；正式验收仍
应同时保存 `/perception/audio_event` 原文，防止只验证了日志而没有验证传输接口。

指令清单建议使用以下逐条记录格式：

| 指令 ID | 播放文本 | 语言 | 期望 EMOTION | 期望 ACTION | 期望 CONTROL | 当前状态 | 计划次数 | 成功次数 | 结果 |
|---|---|---|---|---|---|---|---:|---:|---|
| CORE-001 | 坐下 | zh | NONE | SIT | DO | IMPLEMENTED | 20 |  |  |

一次“指令识别成功”必须满足：同一个 `utterance_id` 最终得到期望的
`emotion/action/control/event_type`，并且期间没有发布错误的可执行动作。只有
`speech.asr_text` 正确但最终标签错误，仍记为失败；KWS 已先发布正确事件且最终相同
事件被 `suppressed_duplicate` 时记为成功。各指令必须分别达到门限，不能用总体平均
准确率掩盖某一条指令不达标。

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
| `dir` | `log` | 文件输出目录 |
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
| `runtime_start` | 节点就绪 | `runtime_mode/providers/speaker_api/config_path/log_file` | 确认模式、配置、真实 Provider 和 API 状态 |
| `interaction_start` | 会话开始 | `source/interaction_id/state` | 确认唤醒或 Service 建立会话 |
| `stage_start` | 开始收音 | `stage/interaction_id/utterance_id` | 确认一句话的计时起点 |
| `stage_complete` | 阶段结束 | `stage/result/latency_ms` | VAD、KWS、ASR、声纹、意图耗时与结果 |
| `event_publish` | 发布音频事件 | `event_type/interaction_id/utterance_id/state/payload` | 用完整 payload 对照 Topic 原文和下游入口 |
| `utterance_complete` | 整句处理结束 | `result/event_type/latency_ms` | 判断最终发布或 KWS 去重 |
| `service_complete` | VoiceTask 返回 | `task_id/task_type/result/latency_ms/task_result` | Service 成败与耗时 |
| `interaction_hold` | 租约申请、续租、释放或到期 | `operation/result/hold_token/reason` | 验证保持租约生命周期 |
| `enrollment_publish` | 发布注册进度 | `result/speaker_id/latency_ms` | 声纹注册阶段结果 |
| `speaker_api_upload` | API 上传处理结束 | `result/speaker_name/audio_valid/has_effective_speech/source_duration_ms/speech_duration_ms/segment_count/latency_ms` | 判断文件校验、VAD 截取、声纹提取和落盘结果 |
| `speaker_management` | 查询、改名或删除 | `operation/result/speaker_name/speaker_count/latency_ms` | 复核声纹管理和运行时索引同步 |
| `interaction_end` | 会话结束 | `reason/interaction_id/state` | 确认超时或手动停止 |

`stage_complete.stage` 当前包括：

- `vad_capture`：`latency_ms` 是从启动本句收音到结果可取的墙钟耗时；
  `audio_duration_ms` 是结果音频长度，两者不是同一个指标。
- `kws`：从本句开始收音到命令检出的耗时。
- `asr`：一次 `transcribe()` 调用耗时；`result=empty` 表示没有有效文本。
- `speaker`：一次声纹检索调用耗时；结果为 `matched/unknown/error`。
- `intent`：规则/RKLLM 意图解析耗时，并记录最终 `event_type` 和来源。

`event_publish.latency_ms` 是事件自身携带的处理延迟，不等同于整句端到端耗时；
端到端处理看 `utterance_complete.latency_ms`。所有耗时单位均为毫秒。

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

以 Event Mock 为例：

```bash
mkdir -p /tmp/marsdog_voice_qa/VOICE-MOCK-001
ros2 launch marsdog_voice_interaction voice.launch.py \
  config_path:=/home/cat/xbb/MarsDogVoiceInteraction/config/voice.mock.yaml \
  log_dir:=/tmp/marsdog_voice_qa/VOICE-MOCK-001
```

真机改用 `voice.yaml`；Pipeline Mock 改用 `voice.pipeline.mock.yaml`。首先检查
`runtime_start`，模式或 Provider 不符合预期时立即停止，不继续出具功能结论。

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
| 声纹识别 | 发布 MASTER 或 STRANGER 身份事件 | `stage_complete stage=speaker latency_ms/confidence` |
| 意图 | 事件的 `action/control/event_type` 符合契约 | `stage_complete stage=intent latency_ms/intent_source` |
| KWS 去重 | 相同最终事件不再发布，`utterance_complete result=suppressed_duplicate` | 同一 `utterance_id` 内检查 |
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
目标指令数 / 已实现数 / 缺失数：
功能覆盖率：
实际执行次数 / 成功次数 / 识别准确率：
输入与步骤：说“坐下”3 次，距离 1 m，环境噪声 xx dB
预期：每次 speech=坐下；最终 EVT_VOICE_COMMAND_SIT；不重复发布
实际：
关联 interaction_id：
关联 utterance_id：
关键事件时间线：
阶段耗时：VAD / KWS / ASR / Speaker / Intent / utterance total
结果：PASS / FAIL / N/A-MISSING_ACCEPTED / BLOCKED-MANIFEST / KNOWN-GAP
最早异常时间和错误：
附件：节点日志、Topic 原文、Service 返回、rosbag、必要时视频
```

批量性能报告至少给出样本量、成功率、P50、P95、最大值，并区分
`vad_capture`、`kws`、`asr`、`speaker`、`intent` 和整句处理，禁止把不同定义的
`latency_ms` 混在同一列。

## 8. 交付给测试团队的文件

建议每个测试版本提供：

1. 本文档 `docs/TESTING_LOG_GUIDE.md`：测试执行和日志判定。
2. `docs/ROS2_CONTRACT.md`：事件、字段、枚举和 Service 权威契约。
3. `docs/HANDOFF.md`：上下游职责和跨项目语义。
4. 三份配置：`voice.yaml`、`voice.mock.yaml`、`voice.pipeline.mock.yaml`。
5. 发布说明：Git commit、构建时间、模型版本/校验值、已知限制和本轮变更。
6. 一份已跑通的示例证据包，证明测试命令和日志提取方式可复现。

语音文本、说话人 ID、声纹样本和注册表属于本地测试/生物特征数据。日志和证据包
按内部敏感数据管理，不提交公开仓库；`data/speakers`、注册表和原始音频不得随代码
交付。
