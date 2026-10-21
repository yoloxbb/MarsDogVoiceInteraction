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

- 完整确定性词库：`config/command_catalog.yaml` 覆盖新表中的 **43 个固定词句行**，共 **43 个路由组、222 条标准词/句**；每条标准入口生成
  10 条受控扩展，共 **2220 条扩展、123 条人工登记变体、2565 条运行时精确匹配入口**。ASR 文本
  命中标准入口或受控扩展后不经过意图模型。21 组核心和其他目录项都只发布目录指定
  的具体事件，不附带 `EVT_VOICE_COMMAND_KNOWN` 摘要；测试人员按
  [COMMAND_CATALOG_TEST_MATRIX.md](COMMAND_CATALOG_TEST_MATRIX.md) 逐条对齐短语和事件。
- 新表49个功能条目：43个固定路由、4个参数句式、1个闲聊兜底、1个声源入口；Sheet1实际行号写入 source_rows。
  行为主/次序列、备注及全局约束保留为元数据，Voice通过不代表下游完成动作。
- 21 组核心训练指令是完整词库的子集。硬件或 pipeline Mock 唤醒发布
  `EVT_VOICE_WAKEUP`；Model Intent 呼名继续发布 `EVT_VOICE_CALL`。词库昵称、
  夸赞、责备分别发布 `EVT_VOICE_CALL`、`EVT_VOICE_COMMAND_PRAISE/SCOLD`。昵称固定
  `should_trigger_behavior_tree=false`；PRAISE/SCOLD 保持 `is_executable=false`，
  但以 `dispatch_role=social_reaction`、`should_trigger_behavior_tree=true` 授权 Tree
  生成一次性社交反应。Voice 发布成功仍不能证明 Action 或硬件已经执行。
- 目录外文本使用 Model Intent `SOCIAL|INTENT|CONTROL` 三轴协议，正式配置模型文件为
  `qwen2_5_5b_rk3588_260903_w8a8.rkllm`。模型先产生不可执行的业务大类；只有同时
  命中标签白名单、ASR 文本动作证据且未被否定，才额外产生可执行具体动作及不可执行
  KNOWN 摘要。不能用模型 INTENT 数量替代词库覆盖率。
- `FETCH/FIND_TOY` 还必须经过 `config/object_targets.yaml` 的 18 类目标物门控；
  未命中目标时 `object_name=NONE`，不得发布可执行 FETCH。
- 流式 KWS 配置56条中文短语，对应14个动作标签；单字命令只由整句 ASR 精确识别。
  独立否定词“不要/别/不准/不许”不计入动作词数；旧版标签不能绕过当前目录。
- Excel第25行重复的“你叫什么名字”合并；“叫两声/说话”统一归第18行 BARK。
- 旧版表外 PLAY_ALONE/INSPECT_TOY/ROLL_OVER 等不再参与本版词库验收。
- 完整复现：运行 `scripts/generate_command_catalog.py --check`、`scripts/generate_kws_keywords.py --check`。
- “去厨房拿狗绳子”应发布一个 FETCH_FROM_LOCATION；地点或物品未知时只出摘要。
- “去找主人”应发布 `person_id=owner` 的可执行找人事件；“去找爸爸/妈妈”未登记，应出 unregistered_person 摘要。

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
| 1 | 以 `command_catalog.yaml` 中 `core=true` 的 21 组为核心清单，每组代表短语播放 20 次，并补测全部别名。 | 即时模式检查 `stage=kws result=published_immediate` 和对应事件；没有 KWS 命中的语句检查 ASR 目录及仲裁。检查 `dispatch_role/specific_event_type/raw_nlu_tag`。 | 每组代表短语至少 17/20，且 21/21 均有结果。业务结果来源一致，同句同事件只发布一次。只有已经接入 Tree/Action 的组可判端到端 PASS。 |
| 2 | 以新表43个固定词句行、共 222 条标准词/句和每条 10 个受控扩展执行覆盖测试。 | 记录目录匹配策略、命令编号、事件和扩展规则，复核事件字段；未命中时记录模型来源。 | 标准词/句每条测试 10 次时，85% 门限至少 **9/10**；扩展规则验收 `2220/2220`。报告源数据 `43/43`、路由组 `43/43`、标准词/句 `222/222`、变体 `123/123`、总入口 `2565`；下游未接入项按 Voice 事件验收。 |
| 3 | 使用唤醒词启动正式会话，并分别播放一条关键词命令和一条目录外语义文本。 | 关键词在 VAD 完成前出现即时命令事件；句尾提供 ASR 和该句独立声纹身份事件。目录外文本没有 KWS 命中时进入词库及意图路由。 | 同一 `interaction_id` 内两条语句各自完整，后台识别期间关键词可以继续发布。 |
| 4 | 机播已知文本，对照 `speech` 事件中的 `asr_text` 和完整 `payload`。 | `stage_complete stage=asr result=ok`；`event_publish event_type=speech`；完整 ROS2 原文。 | `asr_text` 与期望文本一致或符合用例允许的等价转写；JSON 字段符合当前 ROS2 契约。测试表里的 `action/target` 不作为格式标准。 |
| 5 | 对编号 4 的同一 `utterance_id` 检查业务结果。 | 已经发布即时 KWS 命令时，后续提供 ASR 文本和该句身份事件。没有 KWS 命中时检查目录或模型路由。 | 同句同事件没有重复，业务来源一致；模型结果符合三轴约束，具体动作有 `model_action_gate=accepted`。 |
| 6 | 使用专用 immediate 配置，按 55 条中文动作关键词逐条测试即时发布；关闭 KWS 后执行完整目录的 ASR 验收。 | 即时模式检查 `stage=kws result=published_immediate`、`kws_negation_guard=passed` 和事件时间；目录检查 `intent_source=command_lexicon`。 | 动作须经过 250 ms 音频确认窗口，句尾业务结果没有重复。VAD 结束时未满窗口的命令转 ASR，不能提前发布。 |
| 7 | 同会话连续播放三条指令，包括三条不同指令及同一指令重复三次。 | 三个不同 `utterance_id`，逐句检查即时 KWS、`utterance_complete` 和最终事件。 | 三句均正确；每句的同一个具体事件只出现一次，不同语句可以再次发布相同事件。 |
| 8 | 播放“不要过来 / 不要坐下 / 那这里好像有不要过来”，并补测“别动 / 不许动 / 别叫”、相似音和引用句。 | 检查 `stage=kws_guard` 的 `result/reason/keyword/published_event_types`，关联同句全部事件。 | 三条否定录音不得出现可执行命令，ASR 漏掉否定前缀也不得回退执行；有效控制短语整句精确匹配后保留。引用句和未覆盖否定表达单独统计，不能视为已经解决。 |
| 9 | 播放陌生词，随后持续静音，并按正式配置的 `idle_timeout_sec`（30 秒）等待。 | 看到 `command_lexicon result=no_match` 和 UNKNOWN 回退，最后出现同会话 idle 和 `interaction_end`。 | 意图模型和规则提供器关闭；陌生词发布不可执行的 `EVT_VOICE_COMMAND_UNKNOWN`。非空 ASR 仍刷新空闲计时，不发布可执行动作、不崩溃，并在最后一次非空 ASR 后 30 秒静默时待机。 |

正式配置为 KWS 优先：首次有效命中通过否定检查后查看
`kws result=published_immediate`；同句后续关键词为 `suppressed_after_first_kws`。
无有效发布且未被否定拦截时，检查 ASR 的词库 → 参数句式 → UNKNOWN 回退顺序。
补测“去拿球/把球拿来/去房间/到客厅去/罚你站着”：去房间和到客厅去应由完整 ASR 发布
`EVT_VOICE_COMMAND_GO_TO_LOCATION`，其余语句仍按实际 KWS 命中和参数路由处理；有 KWS 发布时采用首个 KWS 命令，
无有效发布时才按整句语义处理。空 ASR 不进入意图模型。
当前正式配置 `interaction.utterance_speaker_enabled=true`，每句均运行声纹，
应逐句看到 `stage_complete stage=speaker` 和对应身份事件。
唤醒声纹由 `wake_speaker_enabled` 独立控制，后续每句使用自身识别结果。
补测主人唤醒后家人/未匹配者交替发言；即时 KWS 为 unknown/pending，以同句身份事件关联最终结果。
参数句式记录 `intent_source=parameterized_command`，词库总入口数不包含这些句式组合。
下面使用正式默认的 `publish_mode=immediate` 验证首次关键词优先与否定拦截。

### 即时 KWS 与 ASR 如何记分

KWS 检测、事件发布延迟和 ASR 转写准确率分别统计。即时 KWS 命令以关键词音频及
`stage=kws result=published_immediate` 为证据；随后 ASR 的转写结果单独验收。
同句的业务结果使用 KWS 来源，后续提供 ASR 文本和该句独立核验的身份事件。

正式即时模式使用独立否定流和 250 ms 音频确认窗口。成功拦截要求同句没有任何
可执行命令，且出现 `kws_guard result=blocked`、`utterance_complete result=blocked_kws_guard`。
`late_veto` / `negation_after_kws_publish` 表示否定到达时动作已经发出，不计为成功拦截。
否定流异常以 `reason=kws_decode_error` 阻止本句动作回退。内部否定信号不发布到 ROS。
通过检查的即时事件带 `slots.kws_negation_guard=passed/kws_guard_window_ms=250.0`。
声学拦截覆盖“不要 / 别 / 不准 / 不许”，
补测“不要过去”及 ASR 漏掉否定前缀的情形。混合多命令、引用句和其他表达仍需负样本验收。

现场录音回归使用真实 KWS 模型离线解码，不启动麦克风或 ROS 节点：

```bash
MARSDOG_NEGATION_TEST_AUDIO_DIR=log/voice_debug python -m pytest -q tests/test_kws_negation_audio.py
python scripts/generate_kws_keywords.py --negation --check
```

录音测试包含 2026-10-13 的三条否定样本及两条原本能够命中 KWS 的正样本，
逐条对照关闭/开启拦截的结果；不代表现场泛化准确率或真机动作验收。
否定词使用“不要”的实际变调 `bú yào`，保持动作检测阈值不变。

正式 KWS 优先链路的软件回归为 `tests/test_kws_priority.py`；用保留的
2026-10-14 六条呼叫录音复核首次命中发布与后续 ASR 去重：

```bash
MARSDOG_PRIORITY_TEST_AUDIO_DIR=/path/to/saved_voice_debug python -m pytest -q tests/test_kws_priority_audio.py
```

六条录音均只发布一条 COME，未调用 RKLLM。其中此前只检出一次 KWS 的两条录音，
分别在 2.18 秒录音的 1.06 秒、3.46 秒录音的 3.14 秒发布；这些是音频进度，
含等待和静音，不代表从说完“过来”开始计算的延迟。
声学词表保持四个已验证词：扩充“不能”的对照回放曾把一条纯“过来”录音在
6.08 秒误判为否定，故未将其纳入正式声学词表。ASR 模型动作门控仍拦截
“不能 / 不可以 / 禁止”等文本；该门控不能撤回此前已经发布的 KWS 命令。

设置 `publish_mode=deferred` 时，记录 `stage=kws result=candidate`、完整 ASR 和
`recognition_arbitration`。该模式的短句确认、精确错写白名单和空 ASR 行为按实际
配置验收。每个用例记录发布模式，统计同句重复事件及错误命令。

可选 deferred 模式的 `repeat_early_command_keys: [COME]` 是整句仲裁的例外。连续说
“过来过来过来过来”，检查 `stage=kws result=published_repeat_early` 是否在
`stage=vad_capture result=voice` 和 ASR 之前出现，且同一 `utterance_id` 仅有一条
可执行业务事件；slots 应含 `recognition_strategy=kws_repeat_early/kws_repeat_hits=2`。
补测单次“过来”、连续“坐下”、混合命令、超过 1.5 秒间隔、
“不要过来过来”、引用句及“把球拿过来拿过来”；前四类不提前发布，否定命中应拦截，
引用与重复参数句需单独统计误触发。已发布后才命中的否定仅记录 `late_veto`，不撤回。
以上软件规则由 `tests/test_repeated_kws.py` 覆盖；真实识别率、提前量及动作响应需现场验证。

已有 2026-10-14 原始录音时可离线复核该可选模式，不打开麦克风或 ROS 节点：

```bash
MARSDOG_REPEAT_TEST_AUDIO_DIR=log/voice_debug python -m pytest -q tests/test_kws_repeat_audio.py
```

录音未放入代码库；应先把对应样本保留到独立目录，再把环境变量指向该目录，
避免运行日志清理导致样本消失。环境变量未设置或某条样本缺失时，该条测试明确跳过。

此前 deferred 重复通道的三条重复呼叫回放均只提前发布一次，三条单次“过来”均未提前发布。
其中 2.82 秒录音在音频进度 2.34 秒发布，另有一条到录音末尾才满足条件；
这些时间包含录音中的等待和句尾静音，不等于说完第二个词后的延迟，也不证明实机动作。

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

ReSpeaker 方向诊断使用默认 INFO 级别，无需切换 DEBUG。修改后需重启 Voice 节点：

- `RESPEAKER_DOA`：串口线程逐条记录 wake／keyword／VAD 原始 JSON（`raw`）、
  `connection_id`、`received_monotonic` 和 `cache_result`。有效样本另带
  `measured_monotonic`、`total_age_ms`；keyword／wake 总年龄包括 `audio_age_ms`。
  `stored` 表示写入呼唤缓存，`out_of_order` 表示测量时刻不比最新样本新，
  `wake_only` 只写入唤醒历史；其他原因包括 `not_ready`、`invalid_state`、
  `invalid_doa`、`invalid_angle_or_age`、`invalid_audio_age`、`numeric_overflow`、`invalid_measured_time`。
- `CALL_DIRECTION`：取角窗口、匹配结果、缓存样本数、`selected`／`latest` 和各自当前年龄。
  两个样本字段均为 `[measured_monotonic, doa, doa_source]`，没有样本时为 null。
  `no_sample` 但 `latest` 非空表示当前缓存有方向，但不在本句窗口内；
  `invalid_window` 表示缺少可靠的音频起止时间，`reader_unavailable`／`not_ready` 表示采集不可用。
  `connection_changed` 表示取角期间连接发生变化。
- 缓存 `stored`／取角 `matched` 不等于发布时仍有效；结合 `event_publish` 的
  `doa_valid`、`doa_age_ms` 判断是否在发布前超过 600 ms。普通事件的
  `wake_angle=0,wake_angle_valid=false` 是模板默认值，不能作方向证据。

```bash
rg 'RESPEAKER_DOA|CALL_DIRECTION|"record":"event_publish"' log/voice_interaction_*.log
```

确定性词库建议使用以下逐条记录格式；核心用例使用 `CORE-*`，全量词库用例另使用
`CATALOG-*` 并记录对应的 `source_rows`。当前 222 条短语的期望值已经整理在
[COMMAND_CATALOG_TEST_MATRIX.md](COMMAND_CATALOG_TEST_MATRIX.md)：

| 指令 ID | 播放文本 | 期望 COMMAND_KEY | 期望 EVENT_TYPE | 期望路由 | Voice 状态 | 下游状态 | 计划次数 | 成功次数 | 结果 |
|---|---|---|---|---|---|---|---:|---:|---|
| CORE-008 | 坐下 | SIT | EVT_VOICE_COMMAND_SIT | command_lexicon | IMPLEMENTED | MAPPED | 20 |  |  |
| CORE-019 | 安静 | QUIET | EVT_VOICE_COMMAND_QUIET | command_lexicon | IMPLEMENTED | KNOWN-GAP | 20 |  |  |

一次“确定性指令识别成功”必须满足：同一个 `utterance_id` 得到
`command_lexicon result=matched`，最终 `command_id/event_type` 符合目录，并且期间
没有发布错误的可执行动作。核心目录也只允许具体特殊事件，不得出现
任何 `EVT_VOICE_*`。ASR 目录被选中后不应再出现该句 `stage=intent`；
KWS 被选中时不应再发布 `command_lexicon` 或 Model Intent 的业务事件。只有
`speech.asr_text` 正确但目录未命中或事件错误，仍记为失败。各组必须分别达到门限。
目录外文本使用 `social/intent/control` 意图判定表。

## 2. 运行模式

| 模式 | 配置 | 是否需要硬件/模型 | 主要用途 |
|---|---|---:|---|
| 正式链路 | `config/voice.yaml` | 是 | 真机唤醒、VAD、KWS、ASR、唤醒声纹及逐句声纹 |
| Event Mock | `config/voice.mock.yaml` | 否 | 直接生成完整 ROS2 事件，验证 Topic 契约和下游消费 |
| Pipeline Mock | `config/voice.pipeline.mock.yaml` | 否 | 走 Mock 唤醒、录音、ASR、声纹和规则意图，验证节点编排与耗时日志 |

`runtime_start.runtime_mode` 是所选模式；`runtime_start.providers` 才是本次进程
实际加载的 Provider。正式配置中部分模型 Provider 不可用时，当前实现可能回退到
Mock Provider，因此真机用例必须确认 `providers` 中没有意外的 `Mock*Provider`。
发现意外回退时，该用例记为环境/启动失败，不能作为真机 PASS 证据。

当前 FastAPI 不包含身份验证，局域网请求不带认证请求头。测试环境必须是可信开发
网络；生产认证不在本轮验收范围内。

### 2.1 测试模式：放宽会话超时（临时）

连续测试时，大量语音本来就识别不出有效命令，会话会按 `idle_timeout_sec` 超时结束，
每说几句就要重新唤醒。需要连续测试时，把 `interaction` 段改成：

```yaml
interaction:
  idle_timeout_sec: 30.0        # 与生产一致；设 0 则完全不因空闲退出
  hold_max_lease_sec: 20.0
  refresh_on_any_speech: true   # 测试模式：有声音就刷新
  max_duration_sec: 0           # 不设绝对上限（与生产一致）
```

与生产的差异：

| 行为 | 生产 | 测试模式 |
|---|---|---|
| 空闲计时刷新条件 | ASR 返回非空文本即刷新，含 NEUTRAL/UNKNOWN 和语义拒识；空白、空结果、异常不刷新 | 任何 VAD 确认的语音（含空 ASR） |
| Event Mock 路径 | `asr_text` 非空即刷新，与事件类型无关 | 每个 mock 交互事件都刷新 |
| 空闲超时 | 30 秒（`idle_timeout_sec: 30.0`），静默超过即结束会话 | 按测试配置；设 0 时不因空闲退出 |
| 会话绝对上限 | 无（`max_duration_sec: 0`） | 同生产 |

确认已生效的三处独立证据：

1. 启动日志出现 `WARNING`：`interaction.refresh_on_any_speech is ON (test mode)…`
2. `VOICE_TRACE` 的 `runtime_start` 记录中 `refresh_on_any_speech=true`
3. `get_interaction_state` 返回体同名字段为 `true`

⚠️ **风险**：测试模式且不设上限时，只要环境持续有噪声或人声，**会话不会自动结束**，
只能靠「停止」命令或 `stop_listening` 退出。这是临时开关，测完必须回滚。

**回滚**：把这两项改回生产值即可，无需改代码。

```yaml
interaction:
  refresh_on_any_speech: false
  idle_timeout_sec: 30.0
  max_duration_sec: 0.0
```

注意：「九项测试的可执行判定矩阵」中第 9 项（未匹配语义结果按超时回到待机）按
`idle_timeout_sec` 验证；若测试期间把空闲超时设成了 0，需先改回正数再测第 9 项。

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

每条 `VOICE_TRACE` 都包含 `timestamp_ms`，表示记录生成时的 13 位 Unix 毫秒时间戳；
VAD、KWS、ASR、声纹、仲裁、词库、意图和事件发布等模块可据此进行跨阶段排序与关联。

## 4. `VOICE_TRACE` 记录表

| `record` | 产生时机 | 核心字段 | 测试用途 |
|---|---|---|---|
| `runtime_start` | 节点就绪 | `runtime_mode/providers/command_lexicon/object_target_routing/kws_arbitration/speaker_api/config_path/log_file/wake_speaker_enabled/utterance_speaker_enabled` | 确认模式、配置、唤醒及逐句声纹开关、真实 Provider 和 API 状态 |
| `interaction_start` | 会话开始 | `source/interaction_id/state` | 确认唤醒或 Service 建立会话 |
| `stage_start` | 开始收音 | `stage/interaction_id/utterance_id` | 确认一句话的计时起点 |
| `stage_complete` | 阶段结束 | `stage/result/latency_ms` | VAD、KWS、ASR、声纹、确定性目录、意图耗时与结果 |
| `stage_skipped` | 可选阶段按配置跳过 | `stage/reason/interaction_id/utterance_id` | `stage=speaker reason=utterance_speaker_disabled` 表示该句未执行声纹核验 |
| `event_publish` | 发布音频事件 | `event_type/interaction_id/utterance_id/state/payload` | 用完整 payload 对照 Topic 原文和下游入口 |
| `utterance_complete` | 整句处理结束 | `result/event_type/selected_source/latency_ms` | 判断最终路由、发布来源和整句结果 |
| `service_complete` | VoiceTask 返回 | `task_id/task_type/result/latency_ms/task_result` | Service 成败与耗时 |
| `interaction_hold` | 租约申请、续租、释放或到期 | `operation/result/hold_token/reason` | 验证保持租约生命周期 |
| `enrollment_publish` | 发布注册进度 | `result/speaker_id/latency_ms` | 声纹注册阶段结果 |
| `speaker_api_upload` | 单文件或批量文件进入声纹业务处理后结束 | `operation/result/speaker_name/sample_id/sample_ids/code/source_sample_rate/stored_sample_rate/conflicting_speaker/similarity/latency_ms` | 判断 WAV、VAD、16 kHz 归一化、声纹冲突校验和落盘结果；不代表完整 HTTP 请求耗时 |
| `speaker_management` | 查询或样本/身份删除变更 | `operation/result/speaker_name/sample_id/shots/deleted_count/latency_ms` | 复核样本 CRUD、身份级删除、全库删除、centroid 和运行时索引同步 |
| `interaction_end` | 会话结束 | `reason/interaction_id/state` | 确认超时或手动停止 |

### 4.1 通用字段

| 字段 | 类型 | 含义和判定方法 |
|---|---|---|
| `record` | string | 追踪记录类型，是筛选日志的第一关键字。 |
| `result` | string | 本次记录的结果。值由不同 `record/stage` 定义，不能跨阶段混用。 |
| `interaction_id` | string | 会话关联 ID；同一轮唤醒到待机期间保持不变。 |
| `utterance_id` | string | 单句关联 ID；同一句的 VAD、KWS、ASR、意图和事件应一致，声纹阶段仅在启用时出现。 |
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
| `idle_timeout_sec` | number | 最后一次非空 ASR 或即时 KWS 命令后的空闲超时；正式配置为 30 秒。后台 ASR 判定期间保持会话有效。 |
| `refresh_on_any_speech` | bool | 测试模式开关；`true` 表示任何 VAD 语音都刷新空闲计时器。生产必须为 `false`，见 2.1。 |
| `max_duration_sec` | number | 单次唤醒会话总时长上限；正式配置为 0，禁用。 |
| `audio_debug` | object | VAD → ASR 调试开关、输出目录、三份 WAV 保存项、`max_utterances` 保留上限、pre-roll A/B 和同模型对照状态。 |
| `providers` | object | 每个 Provider 的 `class/available`；正式测试要求真实 Provider 可用且没有意外 Mock。 |
| `command_lexicon` | object | 词库加载状态和统计：`ready=true/command_count=43/core_command_count=21/phrase_count=222/expansion_enabled=true/variants_per_phrase=10/expanded_phrase_count=2220/total_match_phrase_count=2565/variant_phrase_count=123/fuzzy_matching=true/expansion_profile_count=5/reference_phrase_count=0/source_row_count=43/covered_source_row_count=43`。 |
| `object_target_routing` | object | 目标物目录加载状态。应为 `enabled=true/ready=true/target_count=18`，并记录 `catalog/version/alias_count`。不可用时所有找物模型结果均不得生成具体 FETCH。 |
| `kws_arbitration` | object | 正式配置为 `publish_mode=immediate/arbitration_mode=exclusive`，首次有效命中经过否定检查后发布，同句只采用第一个 KWS 命令；无有效发布且未拦截时使用 ASR 词库/参数句式，未匹配则发布 UNKNOWN。deferred 是可选模式。 |
| `speaker_api` | object | `enabled/ready/address/docs`；启动失败时包含 `error`。 |

### 4.3 `stage_complete` 字段和耗时边界

| `stage` | `result` 可能值 | 阶段专属字段 | `latency_ms` 的准确范围 |
|---|---|---|---|
| `vad_capture` | `voice/silence` | `audio_duration_ms` | 从分配本句并启动收音，到 VAD 结果被节点取出；包含等待说话、有效语音、句尾静音和线程轮询，不是纯 VAD 模型推理耗时。 |
| `kws` | `published_immediate/suppressed_after_first_kws/published_repeat_early/candidate/rejected_catalog_mismatch` | `event_type/command_key/published_event_types/repeat_hits` | 从本句开始收音到关键词取出并处理的耗时；即时或重复提前发布时 `published_event_types` 包含已发事件，普通 deferred 候选时为空。 |
| `kws_guard` | `blocked/late_veto/allowed_exact_control` | `reason/keyword/asr_text/published_event_types` | 否定拦截、迟到否定、有效控制短语的整句精确放行。`late_veto` 不撤回已发动作。 |
| `asr` | `ok/empty/error` | `language/text_length` | 一次 `transcribe()` 调用的总耗时。`ok` 仅表示得到非空文本，不表示文本一定正确。 |
| `speaker` | `matched/unknown/error` | `speaker_id/speaker_confidence` | 声纹 embedding 提取、已注册人员检索和阈值判定的总耗时。 |
| `command_lexicon` | `matched/no_match/unavailable` | `command_key/event_type/catalog_version/catalog_phrase/matched_phrase/match_strategy/expansion_profile/expansion_rule/social/intent/control/action_name/source_rows/core/emit_known_event` | 一次规范化及精确哈希查找的总耗时，`latency_ms` 保留三位小数以记录微秒级查找；原词为 `catalog_exact`，受控扩展为 `rule_expansion`；当前所有词库项均要求 `emit_known_event=false`；`matched` 后结束确定性路由，`no_match` 后走 UNKNOWN 回退。 |
| `recognition_arbitration` | `kws_selected/asr_selected/none_selected` | `selected_source/reason/asr_text/asr_text_length/asr_is_short/kws_candidate_count/kws_candidate_keys/kws_candidate_event_types/catalog_event_type` | 纯规则仲裁函数的耗时。常见 `reason` 为 `no_kws_candidate/short_asr_catalog_agrees/short_asr_unconfirmed_kws/short_asr_catalog_conflict/long_asr_text/configured_kws_priority_alias/empty_asr_fallback_disabled/multiple_kws_candidates`。 |
| `object_target` | `matched/unsupported/unavailable` | `object_name/object_mention/object_matched_alias/object_catalog_version` | 只在 `FETCH/FIND_TOY` 出现。对 ASR 原文进行最长别名优先匹配；只有 `matched` 允许生成可执行 FETCH。 |
| `intent` | `parsed/fallback_unknown` | `event_types/social/intent/control/intent_source` | `_parse_intent()` 与事件路由总耗时；RKLLM 具体动作还应在事件 slots 中出现 `model_action_gate/model_action_gate_reason`。缺少对应文本证据或存在否定时只保留不可执行摘要。 |

补充判定规则：

- `audio_duration_ms` 是交给 ASR/声纹的结果音频长度，不是 VAD 阶段耗时。
- 开启 `audio_debug.enabled` 后，同一句必须在输出目录下生成
  `01_raw_capture.wav/02_vad_segment.wav/03_asr_input.wav`。结合
  `audio_debug` 元数据、`vad_boundary`、`asr_boundary` 和 `vad_join` 判断损失发生在
  原始采集、VAD 边界、额外 pre-roll 或最终 buffer；不得只凭 ASR 文本调阈值。
  例外是**被取消的采集**（会话静默超时、用户打断）：取消后结果本就不会被采用，
  因此不写调试 WAV，此类 utterance 没有 `audio_debug`/`asr_boundary` 日志属预期，
  不能据此判定落盘失败。
- `03_asr_input.wav` 在当前 ASR recognizer `accept_waveform()` 前保存，要求
  `sample_rate=16000/dtype=float32/channels=1/out_of_range_count=0`。
- `intent_source` 常见值为 `command_lexicon/kws/rkllm/`
  `rule_rkllm_compatible/invalid_protocol_fallback`。其中
  `command_lexicon` 和 `kws` 都是模型外的确定性来源。
- 正式 Sherpa 声纹 Provider 的 `speaker_confidence` 是与库中最佳样本的余弦分数，
  未达到阈值而成为 `unknown` 时仍会给出该分数；它不是校准后的身份概率。
  mock Provider 的固定值不能用于声纹阈值标定。声纹是否通过仍以 `result`、
  `speaker_id` 和身份事件为准。

### 4.4 `event_publish` 顶层字段

| 字段 | 类型 | 含义和判定方法 |
|---|---|---|
| `topic` | string | 实际发布目标，正常为 `/perception/audio_event`。 |
| `event_type` | string | 本次发布的具体事件类型；现有 Tree 仍以此精确映射。 |
| `interaction_id` / `utterance_id` | string | 会话和单句关联 ID；会话级事件允许 `utterance_id` 为空。 |
| `state` / `previous_state` / `state_reason` | string | 发布后的状态、前一状态及状态变化原因。 |
| `wake_word` | string | 命中的唤醒词。 |
| `wake_angle` | number | 唤醒方位角，单位度；Voice 不应用安装偏移。 |
| `wake_confidence` / `wake_score_raw` | number | 归一化唤醒置信度和硬件原始分数；原始分数只在完整 `payload` 中保证可见。 |
| `asr_text` / `language` | string | 清洗后的 ASR 文本和语言标识。 |
| `speaker_id` / `speaker_confidence` | string / number | 固定身份 `owner`、`family_member_1`～`family_member_4` 或 `unknown`；正式 Sherpa 模式下为最佳模板余弦分数，并非身份概率。 |
| `social` / `intent` / `control` | string | Model Intent 正式三轴；目录核心指令也携带规范三轴，例如 QUIET 为 `NONE|BARK|STOP`。 |
| `emotion` / `action` | string | 兼容字段；模型事件分别镜像 `social/intent`，具体目录事件的 `action` 保留 `command_key`。新测试不得把它们当正式三轴。 |
| `command_id` / `intent_category` / `intent_source` | string | 命令标识、分类类别及决策来源；目录事件要求 `command_id` 等于目录声明值、`intent_source=command_lexicon`。 |
| `intent_confidence` | number | 意图 Provider 给出的置信度；不能与声纹或唤醒置信度混用。 |
| `nlu_protocol` / `raw_nlu_tag` | string | 协议版本及严格校验后的完整三轴文本。Model Intent 当前不提供校准置信度，不能用 `intent_confidence=0.0` 判失败。 |
| `specific_event_type` / `dispatch_role` | string | KNOWN 摘要指向的具体事件，以及该事件是摘要、具体指令、语义分类还是诊断。 |
| `slots` | array | `[{"key":"...","value":"..."}]`；找物目标使用规范视觉类别 `object_name`。未命中为 `NONE`，同时检查 `object_mention/object_match_source`。 |
| `is_executable` | bool | 当前事件是否表示可执行意图；完整值在 `payload` 中。 |
| `should_trigger_behavior_tree` | bool | 动作指令及词库 PRAISE/SCOLD 社交反应为 `true`；Model Intent 分类事件与 CALL_NAME 为 `false`。Voice 发布成功不等于动作已经完成。 |
| `latency_ms` | number | 事件对应的决策阶段耗时：KWS 选中事件为 VAD 返回后到最终仲裁发布，目录事件为词库查找，Model Intent 事件为意图解析和路由；身份/状态等无独立决策计时的事件可为 0。KWS 首次候选耗时见 slot `kws_candidate_latency_ms` 或 `stage=kws`。它不代表完整端到端耗时。 |
| `payload` | object | 实际发布到 ROS2 Topic 的完整 schema v2 JSON，是事件字段的权威日志副本。 |

完整 `payload` 还包含 `schema_version`、`header`、`response_text`、`danger_type`、
`danger_angle` 等通用字段。其字段类型和枚举以
[ROS2_CONTRACT.md](ROS2_CONTRACT.md) 为准。

### 4.5 其他记录的专属字段

| `record` | 字段 | 含义和判定方法 |
|---|---|---|
| `interaction_start` | `source/state` | 会话来源和进入状态；`source` 常见为 `wakeup/service`。 |
| `utterance_complete` | `result/event_type/event_types/published_event_types/selected_source/reason/latency_ms` | 即时完成为 `published_kws_immediate`，重复提前发布为 `published_kws_repeat_early`，成功拦截为 `blocked_kws_guard`，迟到否定为 `negation_after_kws_publish`；普通 deferred 为 `published_kws_selected`，ASR 目录为 `published_direct_command`。 |
| `service_complete` | `service/task_id/task_type/task_result/error/latency_ms` | 一次 VoiceTask 回调总耗时和完整返回对象。`result=success/failure`。 |
| `interaction_hold` | `operation/hold_token/reason/lease_sec/idle_timer_reset` | `operation=acquire/renew/release/expire`；不同操作只输出适用字段。 |
| `enrollment_publish` | `topic/speaker_id/payload/latency_ms` | 一次录音注册样本的 embedding、进度处理和发布耗时；最终样本还包含落盘及运行时同步。`result=progress/complete`。 |
| `speaker_api_upload` | `operation/speaker_name/shots/sample_id/sample_ids/code/source_sample_rate/stored_sample_rate/conflicting_speaker/similarity/error/latency_ms` | 单文件无 `operation`，批量为 `batch_add`。业务 Handler 总耗时含 WAV、VAD、embedding、冲突校验、落盘和同步；不含 multipart 和网络上传。 |
| `speaker_management` | `operation/speaker_name/sample_id/sample_ids/shots/speaker_removed/deleted_count/deleted_speaker_count/deleted_sample_count/error/latency_ms` | `operation=list/sample_list/sample_get/sample_replace/sample_delete/speaker_delete_all_samples/delete_all_speakers`；变更成功时同步已完成，但运行时识别仍需实际验证。 |
| `interaction_end` | `reason/state/idle_elapsed_sec/idle_timeout_sec/last_activity_reason` | 会话结束原因、单调时钟计算的静默时长及最后活动来源；`reason` 常见为 `interaction_timeout/stop_listening`。 |

### 4.6 总耗时的正确计算

`event_publish.latency_ms` 是产生该事件的决策阶段耗时；ASR 目录具体事件应等于
同句 `command_lexicon.latency_ms`，Model Intent 大类、白名单具体事件和 KNOWN 摘要应等于
同句 `intent.latency_ms`。
即时 KWS 事件的 `latency_ms` 是从本句开始采集到发布的耗时；deferred 事件为 VAD
返回后进入处理到最终仲裁发布的耗时。两种模式分别统计。
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
| 正式链路 | `production` | 所需 Provider 的 `available=true`；`command_lexicon.ready=true/command_count=43/core_command_count=21/phrase_count=222/expanded_phrase_count=2220/total_match_phrase_count=2565/variant_phrase_count=123/fuzzy_matching=true/source_row_count=43/covered_source_row_count=43`；`speaker_api.enabled=true/ready=true` |
| Event Mock | `mock_event` | `mock_event.class=MockEventProvider` 且 `available=true`，`speaker_api.enabled=false` |
| Pipeline Mock | `mock_pipeline` | Wakeup、Audio、ASR、Speaker 为对应的 `Mock*Provider` 且可用；`command_lexicon.ready=true/command_count=43/core_command_count=21/phrase_count=222/expanded_phrase_count=2220/total_match_phrase_count=2565/variant_phrase_count=123/fuzzy_matching=true/source_row_count=43/covered_source_row_count=43`；规则意图可用；KWS 禁用 |

模式、配置路径、Provider 或 API 状态不符合预期时，应停止测试并记为环境/启动失败，
不能继续出具功能 PASS。出现多个 `/voice_interaction` 节点时也必须先清理重复进程。

正式链路还要核对 Model Intent 文件：

```bash
sha256sum /path/to/models/llm/qwen2_5_5b_rk3588_260903_w8a8.rkllm
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
curl -sS -X POST http://127.0.0.1:8091/api/v1/speakers/owner/samples \
  -F 'audio=@/path/to/qa-speaker.wav;type=audio/wav'

curl -sS -X POST http://127.0.0.1:8091/api/v1/speakers/owner/samples/batch \
  -F 'audios=@/path/to/qa-owner-1.wav;type=audio/wav' \
  -F 'audios=@/path/to/qa-owner-2.wav;type=audio/wav'
```

`name` 只能从 `owner`、`family_member_1`、`family_member_2`、
`family_member_3`、`family_member_4` 中选择，FastAPI `/docs` 应显示枚举选择而不是
自由文本输入。

单条样本 CRUD 使用稳定的 `sample_id=1～5`：

```bash
# 新增、列表、详情和下载
curl -sS -X POST http://127.0.0.1:8091/api/v1/speakers/owner/samples \
  -F 'audio=@/path/to/owner-new.wav;type=audio/wav'
curl -sS http://127.0.0.1:8091/api/v1/speakers/owner/samples
curl -sS http://127.0.0.1:8091/api/v1/speakers/owner/samples/1
curl -sS -o /tmp/owner-001.wav \
  http://127.0.0.1:8091/api/v1/speakers/owner/samples/1/audio

# 替换 family_member_1 的第 2 条及删除 owner 的第 1 条
curl -sS -X PUT \
  http://127.0.0.1:8091/api/v1/speakers/family_member_1/samples/2 \
  -F 'audio=@/path/to/family-1-replacement.wav;type=audio/wav'
curl -sS -X DELETE \
  http://127.0.0.1:8091/api/v1/speakers/owner/samples/1

# 身份级删除和显式确认的全库删除
curl -sS -X DELETE \
  http://127.0.0.1:8091/api/v1/speakers/owner/samples
curl -sS -X DELETE \
  'http://127.0.0.1:8091/api/v1/speakers?confirm=true'
```

样本查询应返回 `sample_id/sample_key/audio_url/audio_available/embedding_available`；
下载内容必须与该编号落盘 WAV 一致。接口不提供 `.npy` 或 centroid 下载。替换失败时
原三份文件哈希必须不变；替换成功、删除非最后一条样本后必须重算 centroid 并实际
验证运行时仍能识别该身份。删除最后一条样本应返回 `speaker_removed=true`，人员目录
和注册表记录消失，运行时索引移除且身份槽位可重新注册。

判定成功时必须同时满足：HTTP `201`、`ok=true`、`speech_duration_ms>0`、返回的
`audio_path/embedding_path` 存在，`stored_sample_rate=16000`，且落盘 WAV 为
16 kHz 单声道 PCM16、只包含 VAD 保留的有效语音。不能仅以
接口收到文件作为声纹注册成功。
遇到已注册人员长期识别为 `unknown` 时，按
[声纹低分排查](SPEAKER_DIAGNOSTICS.md) 对比保存样本、网页录音和节点直采音频。

声纹接口需要把 HTTP 层和业务层分开解析：

| 证据 | 能证明的内容 | 不能证明的内容 |
|---|---|---|
| Uvicorn access log | 客户端地址、HTTP 方法、路径和最终状态码 | VAD、embedding 和落盘是否正确 |
| HTTP 响应 JSON | 成功时的 `request_id/ok/name/speaker_role/shots/path/duration`，失败时的 `detail` | ROS 运行时声纹索引是否能实际命中 |
| `speaker_api_upload` | Handler 内 WAV 解析、VAD、embedding、落盘及同步调用结果 | multipart/网络上传耗时，以及 FastAPI 在进入 Handler 前拒绝的请求 |
| `speaker_management` | 人员列表及样本级 GET/PUT/DELETE 业务操作结果 | FastAPI 路径、multipart 或请求体校验阶段直接拒绝的请求 |

以下情况由 FastAPI 在调用声纹业务 Handler 前直接返回，因此通常只有 access log 和
HTTP 响应，**没有** `speaker_api_upload/speaker_management`：

- 缺少 `audio` 或身份路径不在固定枚举：HTTP `422`；
- 空上传文件：HTTP `400`；
- 文件超过配置大小：HTTP `413`；
- 文件名扩展名不是 `.wav`：HTTP `415`；
- 身份路径参数本身不符合接口约束：HTTP `422`。

进入业务 Handler 后发生的 WAV 内容损坏、VAD 无语音、声纹提取失败等，才会同时
看到 `speaker_api_upload result=failure`。业务失败响应采用
`{"detail":{"code":"稳定错误码","error":"错误原因",...}}`；FastAPI 自身的路径或
字段校验仍使用标准 `detail[]`。当前 HTTP 成功响应的
`request_id` 没有写入 `VOICE_TRACE`，只能使用请求时间、客户端地址、人员名称和
Uvicorn access log 关联；不得声称已经通过 `request_id` 完成日志关联。

同时执行以下异常和管理用例：

- 上传非 WAV、截断 WAV、无有效语音和有效段不足 0.5 秒的文件，均应返回 `4xx`，
  且不产生人员目录。
- 主人成功注册后，用同一个人的另一段音频注册家人，应返回 `409`、
  `code=speaker_identity_conflict`，并核对冲突身份、相似度和阈值；同一身份追加应成功。
- 批量上传中混入 1 个无效文件，应返回 `failed_file_index`，整批样本数和目录哈希不变。
- 不带 `confirm=true` 调用全库删除必须返回 `400`，且任何声纹数据不变。
- 请求中附带 `storage_root/path/output_dir` 等字段，必须不能改变配置中的落盘目录。
- 分别建立 `owner` 和 4 个 `family_member_*`，确认恰好 5 个固定身份槽位；提交任意
  自定义姓名或 `family_member_5` 返回 HTTP `422`，且不调用业务 Handler、不落盘。
- 给已有身份追加样本仍成功；旧版顶层 POST、身份 PATCH 和整人 DELETE 不应出现在
  OpenAPI 中，也不得继续作为测试调用入口。
- 对同一人员连续上传 5 个有效样本后，第 6 个返回 HTTP `409`，目录内不得出现
  `006.wav/006.npy`；`GET` 返回 `max_samples_per_speaker=5`。
- 删除 `001` 时 `002` 不得重编号；再次新增应复用最小空闲编号 `001`，已有 `002`
  的内容和样本 ID 保持不变。
- 分别替换 `owner/001` 和 `family_member_1/002`，检查 VAD 后 WAV、对应 embedding、
  centroid、注册表 `shots` 和运行时索引一致；损坏 WAV/无语音替换必须失败且原文件
  不变。
- 删除非最后一条样本后只移除对应 `.wav/.npy`；删除最后一条后释放整个身份槽位。
- `GET` 返回 `count=5/max_speakers=5`、完整 `allowed_names`、空的
  `available_names`，且每项 `role` 正确；逐条删除至最后一条后，身份目录及运行时
  索引均移除，随后可以重新注册该空闲槽位。

声纹识别事件按下表判定；三类事件都在 `/perception/audio_event` 上发布，由行为树等
下游消费：

| 测试声纹 | `speaker_id` | 期望 `event_type` |
|---|---|---|
| 主人 | `owner` | `EVT_VOICE_MASTER_ID` |
| 任一家人 | `family_member_1`～`family_member_4` | `EVT_VOICE_FOLK_ID` |
| 未注册/未匹配人员 | `unknown` | `EVT_VOICE_UNMASTER_ID` |

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
| 唤醒 | `interaction_start` 后发布 `EVT_VOICE_WAKEUP` | 唤醒事件的角度、置信度；硬件响应时间由外部操作时间对照 |
| VAD | `stage_complete stage=vad_capture result=voice` | `latency_ms`、`audio_duration_ms` |
| KWS 发布 | 正式即时模式通过否定检查与 250 ms 音频窗口后只发布同句首个 KWS 命令；deferred 的 COME 重复通道为可选配置 | `stage_complete stage=kws result=published_immediate/suppressed_after_first_kws`、`stage=kws_guard` |
| ASR | 发布 `speech`，`asr_text` 与实说内容对照 | `stage_complete stage=asr latency_ms` |
| 声纹识别 | 正式配置逐句核验：`owner` 发布 MASTER，`family_member_*` 发布 FOLK，未匹配/历史名称发布 UNMASTER | 每句检查 `stage_complete stage=speaker latency_ms/speaker_id/speaker_confidence` 和对应身份事件 |
| 完整确定性词库 | 所有项只发布目录具体特殊事件，不附带 KNOWN 摘要；该句不执行 Intent | `command_lexicon matched/latency_ms`，并检查唯一 `dispatch_role=specific_command` 和 `specific_event_type` |
| 目录外意图 | 三轴及事件顺序符合契约；仅白名单具体动作可执行 | `command_lexicon no_match` 后检查 `stage=intent social/intent/control/event_types/latency_ms`；找物类还要检查 `stage=object_target` |
| KWS/ASR 业务来源 | 即时或重复提前发布后，同句保留 ASR 文本和逐句身份事件。未发布且未被否定拦截时执行 ASR 业务路由。其他 deferred 语句按整句仲裁配置执行 | KWS 发布、否定标记、同句后续事件及 `utterance_complete`；普通 deferred 的 `recognition_arbitration` |
| 空闲超时/主动结束 | 静默超过 `idle_timeout_sec`（正式配置 30 秒）后自动结束；`stop_listening` 发布匹配 ID 的 idle 并关闭采集 | `idle_timeout_sec=30`、`max_duration_sec=0`；`interaction_end reason=interaction_timeout` 或 `stop_listening` |
| 手动监听 | VoiceTask 返回成功并带当前 ID | `service_complete latency_ms/task_result` |
| 会话保持 | hold 后不超时；release/租约到期后恢复超时 | Service 结果、`interaction_hold`、结束时间 |
| 声纹注册 | 注册 Topic 连续进度，最终 `done=true` | `enrollment_publish result=complete/latency_ms` |
| 声纹 API 上传 | `runtime_start.speaker_api.ready=true`，HTTP 201，`audio_valid/has_effective_speech=true`，VAD 后 WAV/embedding/centroid 均落盘 | `speaker_api_upload result=success` 及源音频/有效语音/总耗时 |
| 声纹 16 kHz 归一化 | 上传 8/44.1/48 kHz PCM16 WAV 后均返回 `stored_sample_rate=16000`，下载 WAV 实测为 16 kHz 单声道 PCM16 | HTTP 响应、下载 WAV 元数据和 `speaker_api_upload source_sample_rate/stored_sample_rate` |
| 跨身份防重复注册 | owner 已注册时，同人音频注册 family 返回 409 和 `speaker_identity_conflict`，无新文件；现场注册遵守同样规则 | HTTP `detail.conflicting_speaker/similarity/similarity_threshold` 和失败 trace |
| 同身份样本一致性 | 同身份追加、批量上传及有其他样本的替换，低于一致性阈值返回 `speaker_sample_inconsistent`，不改旧样本 | HTTP 409、失败样本编号和相似度 |
| 模型故障拒识 | 声纹模型不可用时返回 `unknown/unavailable`，不得因库中存在 owner 而产生模拟匹配 | `stage_complete stage=speaker reason=unavailable` |
| 身份模糊拒识 | 第一、第二身份分差不足或同分时返回 `unknown/ambiguous_identity` | `reason/score_margin`；真实录音分别统计误识与拒识 |
| 有效语音时长 | 250 ms 的 VAD 片段即使添加 500 ms 前后音频也应拒绝注册 | HTTP 422；`speech_duration_ms` 不计额外补音和段间静音 |
| 连续说话采集 | 上一句后台识别期间，第二句继续进行 VAD、KWS 和即时发布；等待整句识别的音频受容量和保留时间限制 | 两句不同的 `utterance_id`，即时事件时间和 `speech_processing_queue` |
| 等待与说话超时 | 等待接近 8 秒才开始说话，开始后仍有独立的 8 秒上限 | `capture_end_reason=vad_complete/speech_limit/wait_timeout` |
| 音频缓存异常 | 溢出、过期或录音丢帧时丢弃受影响句子，并等静音边界后恢复 | `capture_end_reason=audio_gap/audio_resync_timeout`；不发布残句命令 |
| 无数据与取消 | sounddevice/arecord 无数据能超时退出；取消关闭设备并清空待处理音频 | 后端超时日志；新会话不出现旧音频或旧 KWS 候选 |
| ROS 上传识别 | 1 秒双声道按 1 秒单声道处理；非 PCM16、空数据、无语音不使用上一条音频 | `verify_speaker` 的错误与识别结果 |
| 批量新增/批量删除 | 多文件全部通过才统一落盘；单身份全删和带确认的全库删除后目录、注册表及运行时索引一致 | `speaker_api_upload operation=batch_add`、`speaker_management operation=speaker_delete_all_samples/delete_all_speakers` |
| 声纹身份限制与管理 | 固定 5 个身份槽位；自由名称返回 422；列表和样本删除与目录及运行时索引一致 | HTTP 状态码和 `speaker_management operation/result/latency_ms` |
| 单人样本限制 | 每人最多 5 个；第 6 次同名上传返回 409 且无 `006` 文件 | HTTP 状态码、列表 `shots/max_samples_per_speaker` 和目录文件数 |
| 单条声纹样本 CRUD | 稳定 ID 查询/WAV 下载正确；替换或删除后 centroid、注册表和运行时索引同步；删最后一条释放身份 | HTTP 状态码及 `speaker_management operation=sample_list/sample_get/sample_replace/sample_delete` |

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
源数据行 / 路由组 / 标准词句 / 扩展 / 变体 / 总入口：43/43 / 43/43 / 222/222 / 2220/2220 / 123/123 / 2565
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
2. `docs/COMMAND_CATALOG_TEST_MATRIX.md`：222 条标准词/句、扩展规则与期望事件表。
3. `docs/ROS2_CONTRACT.md`：事件、字段、枚举和 Service 权威契约。
4. `docs/HANDOFF.md`：上下游职责和跨项目语义。
5. 三份运行配置和确定性目录：`voice.yaml`、`voice.mock.yaml`、
   `voice.pipeline.mock.yaml`、`command_catalog.yaml`。
6. 发布说明：Git commit、构建时间、模型版本/校验值、已知限制和本轮变更。
7. 一份已跑通的示例证据包，证明测试命令和日志提取方式可复现。

语音文本、说话人 ID、声纹样本和注册表属于本地测试/生物特征数据。日志和证据包
按内部敏感数据管理，不提交公开仓库；`data/speakers`、注册表和原始音频不得随代码
交付。
