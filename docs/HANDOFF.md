# 语音项目交接说明

> Voice 对接基线：2026-09-08 / AudioEvent schema v2

## 1. 本项目负责什么

语音节点负责唤醒、录音/VAD、流式 KWS、ASR、声纹识别、完整产品词库匹配、
非词库文本的意图分类，以及一次语音会话的生命周期。当前词库覆盖 116 条
源数据，另新增“自己去玩吧”，共 82 个路由组和 156 条标准中文短语；每条另生成 10 个受控扩展，
再加 70 条人工登记的变体，共 1786 个精确匹配入口；其中 19 组是核心指令子集。
它只发布“听见了什么”和
“会话状态”，不订阅视觉数据，也不直接发布 `/cmd_vel` 或调用动作系统。

- 主节点：`voice_interaction`
- 入口：`marsdog-voice-interaction`
- 配置：`config/voice.yaml`

## 2. 对外接口

| 方向 | 接口 | 类型 | QoS/说明 |
|---|---|---|---|
| 发布 | `/perception/audio_event` | `std_msgs/msg/String` JSON | RELIABLE, KEEP_LAST 10 |
| 发布 | `/perception/voice/enrollment_event` | `std_msgs/msg/String` JSON | RELIABLE, KEEP_LAST 10 |
| 提供 | `/perception/voice/task` | `marsdog_voice_interaction/srv/VoiceTask` | 管理声纹和监听状态 |
| 提供 | `GET /api/v1/speakers` | FastAPI JSON | 查询固定身份槽位及样本数量 |
| 提供 | `/api/v1/speakers/{name}/samples...` | FastAPI multipart/JSON/WAV | 单条/批量样本新增、查询、WAV 下载、替换、单条及整身份删除；新增/替换校验跨身份冲突，变更后重算 centroid 并同步运行时索引 |
| 提供 | `DELETE /api/v1/speakers?confirm=true` | FastAPI Query/JSON | 显式确认后删除全部固定身份声纹并同步运行时索引 |

完整 JSON 字段和任务参数见 [ROS2_CONTRACT.md](ROS2_CONTRACT.md)，测试日志、取证
步骤和报告模板见 [TESTING_LOG_GUIDE.md](TESTING_LOG_GUIDE.md)。跨项目总契约归档
位于 `/home/cat/xbb/MarsDogVisionInteraction/docs/integration/`。

## 3. 下游依赖的关键语义

### 会话 ID

- 唤醒成功后创建 `interaction_id`。
- 从 `EVT_VOICE_WAKEUP` 到最终 `EVT_STATE_CHANGED(state=idle)` 必须保持同一个 `interaction_id`。
- 每句话使用新的 `utterance_id`；同句话的 KWS、声纹、speech 和最终路由结果共享该 ID。

### 声纹身份事件

| `speaker_id` | 发布事件 | 下游含义 |
|---|---|---|
| `owner` | `EVT_VOICE_MASTER_ID` | 主人声纹 |
| `family_member_1`～`family_member_4` | `EVT_VOICE_FOLK_ID` | 家人声纹 |
| `unknown` 或未匹配 | `EVT_VOICE_UNMASTER_ID` | 非主人且非固定家人身份 |

这些事件发布到 `/perception/audio_event`，由行为树等下游消费。Voice 只负责身份识别
和事件发布，不直接调用动作系统。

### Voice 下发、供行为树路由的事件

```text
EVT_VOICE_WAKEUP
EVT_VOICE_COMMAND_SIT / LIE_DOWN / STAND_UP / WAIT / COME / FOLLOW
EVT_VOICE_COMMAND_SHAKE_HAND / HIGH_FIVE / ROLL_OVER / SPIN / RETURN
EVT_VOICE_COMMAND_DROP / PLAY_DEAD / BRING / FETCH / STOP
EVT_STATE_CHANGED
```

下表是 Voice 的发布契约，不等于当前 Tree 已全部接入。词库命中结果仍发布到
`/perception/audio_event`，进入下游事件路由/行为树，
不由动作系统直接消费。语音项目可以保证事件和 `action_name`正确发布，
但不能代替行为树的事件白名单、Behavior 映射和动作项目的 `ACT_*` 实现。

当前行为树源码已有 11 个核心指令事件映射：

```text
COME / FOLLOW / SIT / LIE_DOWN / PLAY_DEAD / STAND_UP
SHAKE_HAND / HIGH_FIVE / SPIN / ROLL_OVER / DROP
```

`EVT_VOICE_WAKEUP` 现在只表示硬件或 pipeline Mock 唤醒。Model Intent 呼名仍发布
下游既有的 `EVT_VOICE_CALL_NAME`；词库昵称调用发布
`EVT_VOICE_COMMAND_CALL_NAME`（`CMD_CALL_NAME`）。词库夸赞/责备分别发布
`EVT_VOICE_COMMAND_PRAISE/SCOLD`，不占用 Model Intent 的
`EVT_VOICE_PRAISE/SCOLD`。三类词库社交事件均保持 `is_executable=false`；其中
昵称固定 `should_trigger_behavior_tree=false`，不得直接唤醒或执行通用动作；
PRAISE/SCOLD 则固定 `dispatch_role=social_reaction`、
`should_trigger_behavior_tree=true`，授权 Tree 生成一次性社交反应。该权限只证明
事件允许进入 Tree，仍不能把 Voice 发布成功当作 Action 或硬件已经执行。
其中原 19 组核心指令里尚未补齐的 8 组是：

```text
WALK / GO_OUT / GO_HOME / APPROACH / BACK_UP
STAND_STILL / HOLD_POSITION / QUIET
```

在行为树完成映射前，所有未映射组只能验收到“Voice 正确发布 Topic 事件”，不能据此
判定动作已经执行。特别地，`HOLD_POSITION` 是保持当前姿态，不能映射成全局急停；
`QUIET` 是停止发声，也不能映射成底盘急停。

Model Intent 沿用已交付下游的业务事件名；明确的姿态、移动、声音等动作必须同时
通过标签白名单、ASR 文本动作证据和否定语义拦截，才复用对应
`EVT_VOICE_COMMAND_*`。业务分类和 KNOWN 摘要不可执行，只有通过门控的具体动作
事件可设置 `should_trigger_behavior_tree=true`。

`EVT_VOICE_COMMAND_FOLLOW` 必须携带当前 `interaction_id`。行为树收到后把动作系统切到持续 `follow_owner` 模式；该模式一直保持到语音节点发布匹配会话的 `EVT_STATE_CHANGED(state="idle")`。

状态结束原因当前为：

- `interaction_timeout`：最后一次非空 ASR 后静默超过 `idle_timeout_sec`（正式配置
  20 秒）即触发；纯 VAD、空 ASR 和缓存的 KWS 候选不续期。总时长上限正式配置为 0，
  不设硬上限。
- `stop_listening`：Service 主动结束。

行为树进行唤醒转向、视觉锁定或靠近期间，可调用 VoiceTask 的
`hold_interaction` 暂停空闲终止。请求必须精确携带当前 `interaction_id`、稳定
`hold_token` 和有限 `lease_sec`；同 token 重复调用会续租。到达并准备继续对话
时调用 `release_interaction_hold(reset_idle_timer=true)`，从释放时重新等待配置的超时时间。
租约不会屏蔽录音、流式 KWS、STOP 或 `stop_listening`，会话终止时全部租约自动
清除。可用 `get_interaction_state` 查询当前 ID 和有效租约。

### 确定性词库、KWS 和唯一来源仲裁

ASR 得到文本后，节点先使用 `config/command_catalog.yaml` 做规范化后的整句精确匹配。
目录以产品表的 116 条数据行为覆盖基线，连同新增“自己去玩吧”，共 156 条标准中文短语，
每条按配置生成 10 个受控扩展，并归并成 82 个稳定路由组。标准短语或扩展命中时
直接发布该组配置的 `event_type`，并跳过 RKLLM/
规则意图；未命中才进入意图识别。词库中的“回来”明确归为 `COME`，不再归为旧版
`RETURN`。

新增“自己去玩吧”使用 `CMD_PLAY_ALONE` / `EVT_VOICE_COMMAND_PLAY_ALONE`，
语义为“去随机位置自己玩”，`slots.action_name=ACT_PLAY_ALONE` 为行为参考元数据。
下游需按完整事件名接入；Voice 的可执行标志不代表 Tree/Action 已支持。
该词条无产品表行号，保留原产品表 `source_row_count=116`。

产品表的 138 条英文仅作参考元数据，当前不参与确定性匹配。原因是表内存在
`Good dog` 这类跨分类重复表达，在产品给出唯一归属前不能盲目直发。

KWS 在 VAD 结束前只缓存候选，不发布业务事件。ASR 完成后由 Voice 在 KWS 和 ASR
链路之间选择唯一结果来源：普通短指令只有在 ASR 词库命中同一事件时才选择 KWS；
冲突、未确认、多个候选或空 ASR 均不允许 KWS 单独执行。长句选择 ASR。仅
`吃罐罐→去滚罐`、`去拿→去哪` 两个已确认的精确错写组合允许 KWS 覆盖。仲裁记录为
`stage_complete stage=recognition_arbitration`。词库或 KWS 指令无论由哪一来源选中，
都只发布目录指定的具体特殊事件，不附带 KNOWN 摘要。下游仍应按
`interaction_id + utterance_id + event_type` 做幂等保护。

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
| `interaction.idle_timeout_sec` | 正式配置为 20 秒，从最后一次非空 ASR 开始计算；0 或负数 = 禁用空闲超时 |
| `interaction.max_duration_sec` | 正式配置为 0，禁用总时长上限；正数时不被活动或租约延长 |
| `interaction.refresh_on_any_speech` | 测试专用，生产为 `false` 时非空 ASR 文本即刷新，不要求语义接受；`true` 时仅 VAD 检测到语音也会刷新 |
| `interaction.hold_max_lease_sec` | 单次会话保持租约上限 20 秒，调用方需定期续租 |
| `topics.*` | 对外 Topic/Service 名称 |
| `speaker_api.*` | 当前为 `0.0.0.0:8091`，无身份验证，仅限可信开发局域网 |
| `providers.wakeup` | 讯飞串口唤醒板 `/dev/ttyACM0` |
| `providers.audio` | 16 kHz VAD 和录音 |
| `providers.kws` | 流式关键词命令 |
| `providers.asr` | 当前为 SenseVoice INT8 ONNX；provider 实现仍支持其他 Sherpa ASR 模型 |
| `providers.speaker` | 声纹模型和阈值 |
| `command_lexicon` | 完整产品词库（116 条源数据及新增词条/82 个路由组/156 条标准词句/1560 条受控扩展/70 条变体/19 组核心子集）开关、目录路径和 `fuzzy_matching` 同音兜底 |
| `providers.intent_*` | Model Intent 优先、三轴兼容规则回退 |

配置文件中的文件和目录均使用相对于 YAML 所在目录的路径：模型默认通过
`../../models` 指向项目同级的 `models/`，注册数据通过 `../data` 指向本项目
`data/`。FastAPI 上传的有效语音保存到
`data/speakers/<固定身份>/<序号>.wav`，固定身份只能是 `owner` 和
`family_member_1`～`family_member_4`，对应 embedding 使用同名 `.npy`。存储路径
中的 WAV 统一为 16 kHz、单声道、16-bit PCM；源文件允许 8～96 kHz。新样本与其他
身份任一样本达到 `speaker_api.cross_identity_similarity_threshold` 时拒绝写入，
避免同一个人同时占用主人和家人身份。批量新增先全量校验再落盘，失败不会留下半批
数据。存储根目录只能来自 `storage.root`，接口无权覆盖；身份槽位总数固定为 5，单个身份的声纹样本
数也硬限制为 5。不得把模型二进制或用户声纹数据复制到其他
项目。当前 FastAPI 认证模块已移除，只能部署在可信开发局域网；生产认证方案后续
另行设计。

## 6. 修改接口时必须回归

- 唤醒事件包含有限数值的原始 `wake_angle`，单位为度，
  `header.frame_id=microphone_array`。Voice 不应用安装 offset/sign；动作项目是
  唯一标定所有者，消费时只允许应用一次安装零偏和方向正负标定。
- `wake_confidence` 始终在 `[0,1]`；硬件原始分数保存在 `wake_score_raw`。
- 同一会话 ID 不在中途变化。
- FOLLOW 事件只发布一次有效指令，且会话结束必有 idle 状态事件。
- 19 组核心目录指令和其他词库/KWS 指令都只发布各自的具体特殊事件，不得额外发布
  `EVT_VOICE_COMMAND_KNOWN`。
- Model Intent `SOCIAL|INTENT|CONTROL` 先路由业务大类；同时命中动作白名单、ASR
  文本动作证据且未被否定时，才按“社交大类 → 可执行具体动作 →
  `EVT_VOICE_COMMAND_KNOWN` 摘要”发布。动作白名单可与
  词库/KWS 共用具体 `EVT_VOICE_COMMAND_*`；词库社交事件使用独立名称，不占用模型
  业务分类事件。只有具体动作事件可执行；大类和摘要不可执行。`NONE|NONE|NONE` 发布不可执行的
  `EVT_VOICE_NEUTRAL`。
- `FETCH/FIND_TOY` 还受 `object_targets.yaml` 的 18 类视觉目标门控。命中后
  `EVT_VOICE_COMMAND_FETCH` 携带规范 `slots.object_name` 并可执行；未命中写入
  `object_name=NONE` 且只发布原业务大类。Tree/Action 应按规范类别请求视觉目标，
  Voice 不生成 `target_track_id`。
- Topic 仍为 RELIABLE depth 10，与行为树订阅匹配。
- 新增或修改确定性指令时，同时更新 `command_catalog.yaml`、`voice_event_types`、
  契约和测试，并通知行为树负责人增加事件白名单与 Behavior 映射、动作负责人确认
  Behavior 到 `ACT_*` 的映射；不能只改 Voice 后就宣称端到端可执行。

## 7. 明确不属于本项目的问题

- 人脸框抖动、目标选择：视觉项目。
- 命令优先级、排队和抢占：行为树项目。
- 跟随速度、死区、底盘运动：动作项目。
- 语音节点只保证正确发布会话事件，不能绕过行为树直接控制动作。
