# 语音项目交接说明

> 对接基线：2026-08-04 / 多项目契约 1.0.0

## 1. 本项目负责什么

语音节点负责唤醒、录音/VAD、流式 KWS、ASR、声纹识别、完整产品词库匹配、
非词库文本的意图分类，以及一次语音会话的生命周期。当前词库覆盖 116 条
源数据，归并为 81 个路由组和 155 条标准中文短语；每条另生成 10 个受控扩展，
共 1705 个精确匹配入口；其中 19 组是核心指令子集。
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
| 提供 | `POST /api/v1/speakers` | FastAPI multipart | 上传 WAV，经 VAD 后注册并本地落盘 |
| 提供 | `GET/PATCH/DELETE /api/v1/speakers...` | FastAPI JSON | 列表、变更固定身份槽位和删除声纹 |

完整 JSON 字段和任务参数见 [ROS2_CONTRACT.md](ROS2_CONTRACT.md)，测试日志、取证
步骤和报告模板见 [TESTING_LOG_GUIDE.md](TESTING_LOG_GUIDE.md)。跨项目总契约归档
位于 `/home/cat/xbb/MarsDogVisionInteraction/docs/integration/`。

## 3. 下游依赖的关键语义

### 会话 ID

- 唤醒成功后创建 `interaction_id`。
- 从 `EVT_VOICE_CALL_NAME` 到最终 `EVT_STATE_CHANGED(state=idle)` 必须保持同一个 `interaction_id`。
- 每句话使用新的 `utterance_id`；同句话的 KWS、声纹、speech 和最终路由结果共享该 ID。

### 声纹身份事件

| `speaker_id` | 发布事件 | 下游含义 |
|---|---|---|
| `owner` | `EVT_VOICE_MASTER_ID` | 主人声纹 |
| `family_member_1`～`family_member_4` | `EVT_VOICE_FOLK_ID` | 家人声纹 |
| `unknown`、未匹配或历史自由名称 | `EVT_VOICE_UNMASTER_ID` | 非主人且非固定家人身份 |

这些事件发布到 `/perception/audio_event`，由行为树等下游消费。Voice 只负责身份识别
和事件发布，不直接调用动作系统。

### 行为树直接消费的事件

```text
EVT_VOICE_CALL_NAME
EVT_VOICE_COMMAND_SIT / LIE_DOWN / STAND_UP / WAIT / COME / FOLLOW
EVT_VOICE_COMMAND_SHAKE_HAND / HIGH_FIVE / ROLL_OVER / SPIN / RETURN
EVT_VOICE_COMMAND_DROP / PLAY_DEAD / BRING / FETCH / STOP
EVT_STATE_CHANGED
```

词库命中结果仍发布到 `/perception/audio_event`，进入下游事件路由/行为树，
不由动作系统直接消费。语音项目可以保证事件和 `action_name`正确发布，
但不能代替行为树的事件白名单、Behavior 映射和动作项目的 `ACT_*` 实现。

当前行为树源码已有 11 个核心指令事件映射：

```text
COME / FOLLOW / SIT / LIE_DOWN / PLAY_DEAD / STAND_UP
SHAKE_HAND / HIGH_FIVE / SPIN / ROLL_OVER / DROP
```

另外，`EVT_VOICE_CALL_NAME` 已有下游路由；`EVT_VOICE_PRAISE` 和
`EVT_VOICE_SCOLD` 进入现有情绪计算链路，使用 `control=NONE`、
`should_trigger_behavior_tree=false`，不应强行转成直接动作。因此，81 个路由组中
当前可确认 14 个已有对应的下游入口（11 个核心动作 + 呼名 + 夸赞 + 责备）；
其余 67 个路由组在 Voice 中已可发布，但仍需 Tree/Action 按产品动作表逐项对齐。
其中原 19 组核心指令里尚未补齐的 8 组是：

```text
WALK / GO_OUT / GO_HOME / APPROACH / BACK_UP
STAND_STILL / HOLD_POSITION / QUIET
```

在行为树完成映射前，所有未映射组只能验收到“Voice 正确发布 Topic 事件”，不能据此
判定动作已经执行。特别地，`HOLD_POSITION` 是保持当前姿态，不能映射成全局急停；
`QUIET` 是停止发声，也不能映射成底盘急停。

`EVT_VOICE_COMMAND_FOLLOW` 必须携带当前 `interaction_id`。行为树收到后把动作系统切到持续 `follow_owner` 模式；该模式一直保持到语音节点发布匹配会话的 `EVT_STATE_CHANGED(state="idle")`。

状态结束原因当前为：

- `interaction_timeout`：最后一次有效语音后默认 10 秒无新语音。
- `stop_listening`：Service 主动结束。

行为树进行唤醒转向、视觉锁定或靠近期间，可调用 VoiceTask 的
`hold_interaction` 暂停空闲终止。请求必须精确携带当前 `interaction_id`、稳定
`hold_token` 和有限 `lease_sec`；同 token 重复调用会续租。到达并准备继续对话
时调用 `release_interaction_hold(reset_idle_timer=true)`，从释放时重新等待 10 秒。
租约不会屏蔽录音、流式 KWS、STOP 或 `stop_listening`，会话终止时全部租约自动
清除。可用 `get_interaction_state` 查询当前 ID 和有效租约。

### 确定性词库、KWS 和唯一来源仲裁

ASR 得到文本后，节点先使用 `config/command_catalog.yaml` 做规范化后的整句精确匹配。
目录以产品表的 116 条数据行为覆盖基线，将斜杠别名展开为 155 条标准中文短语，
每条按配置生成 10 个受控扩展，并归并成 81 个稳定路由组。标准短语或扩展命中时
直接发布该组配置的 `event_type`，并跳过 RKLLM/
规则意图；未命中才进入意图识别。词库中的“回来”明确归为 `COME`，不再归为旧版
`RETURN`。

产品表的 138 条英文仅作参考元数据，当前不参与确定性匹配。原因是表内存在
`Good dog` 这类跨分类重复表达，在产品给出唯一归属前不能盲目直发。

KWS 在 VAD 结束前只缓存候选，不发布业务事件。ASR 完成后由 Voice 在 KWS 和 ASR
链路之间选择唯一结果来源：短指令可选择唯一 KWS 候选，长句选择 ASR；ASR 目录结果
与 KWS 冲突时选择 ASR，ASR 为空且只有一个候选时允许 KWS 回退。仲裁记录为
`stage_complete stage=recognition_arbitration`。核心指令无论由哪一来源选中，都可能
按契约发布 KNOWN 摘要和一个具体事件；这两条属于同一结果组。下游仍应按
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
| `interaction.idle_timeout_sec` | 10 秒，从最后一次有效语音开始计算 |
| `interaction.hold_max_lease_sec` | 单次会话保持租约上限 30 秒，调用方需定期续租 |
| `topics.*` | 对外 Topic/Service 名称 |
| `speaker_api.*` | 当前为 `0.0.0.0:8091`，无身份验证，仅限可信开发局域网 |
| `providers.wakeup` | 讯飞串口唤醒板 `/dev/ttyACM0` |
| `providers.audio` | 16 kHz VAD 和录音 |
| `providers.kws` | 流式关键词命令 |
| `providers.asr` | Paraformer ASR |
| `providers.speaker` | 声纹模型和阈值 |
| `command_lexicon` | 完整产品词库（116 条源数据/81 个路由组/155 条标准词句/1550 条受控扩展/19 组核心子集）开关和目录路径 |
| `providers.intent_*` | Model K 优先、三轴兼容规则回退 |

配置文件中的文件和目录均使用相对于 YAML 所在目录的路径：模型默认通过
`../../models` 指向项目同级的 `models/`，注册数据通过 `../data` 指向本项目
`data/`。FastAPI 上传的
有效语音保存到 `data/speakers/<固定身份>/<序号>.wav`，固定身份只能是 `owner` 和
`family_member_1`～`family_member_4`，对应 embedding 使用同名 `.npy`。存储路径
只能来自 `storage.root`，接口无权覆盖；身份槽位总数固定为 5，单个身份的声纹样本
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
- 19 组核心目录指令按顺序发布不可执行的 `EVT_VOICE_COMMAND_KNOWN` 摘要和可执行的
  具体事件；行为树只能用具体事件触发动作，不能把摘要再次当动作候选。
- Model K `SOCIAL|INTENT|CONTROL` 先路由业务大类；命中显式动作白名单时按“社交
  大类 → 可执行具体动作 → `EVT_VOICE_COMMAND_KNOWN` 摘要”发布。只有具体动作事件
  可执行；大类和摘要不可执行。`NONE|NONE|NONE` 发布不可执行的
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
