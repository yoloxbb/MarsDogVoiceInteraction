# MarsDog Voice Interaction

默认使用 ReSpeaker Flex（唤醒 + 录音）配置 `config/voice.respeaker.yaml`，见 [适配、启动和验收说明](docs/RESPEAKER_INTEGRATION.md)。

MarsDog 的独立语音交互 ROS2 包，负责从唤醒到意图事件发布的完整语音链路：

唤醒串口产生唤醒事件后，麦克风音频同时进入 Silero VAD 和流式 KWS。正式配置使用
KWS 优先模式（`publish_mode=immediate`）：第一次有效命中通过否定检查后立即发布
`/perception/audio_event`，同句只采用这一个 KWS 业务结果，不再调用意图模型。
VAD 完成片段后，后台串行执行 SenseVoice ASR，下一句继续录音与 KWS。普通语句的
3D-Speaker 核验由 `interaction.utterance_speaker_enabled` 控制，正式配置启用，
每句使用该句音频独立识别并发布身份结果。唤醒时的说话人识别由
`wake_speaker_enabled` 单独控制，正式配置启用；唤醒结果只描述唤醒者，后续语句按各自声纹结果下发。
同句没有有效 KWS 发布且未被否定拦截时，ASR 依次进入完整词库、取物/地点句式、
未匹配的语句发布 UNKNOWN；正式配置不启用规则意图或 RKLLM 意图模型。

项目只负责“听见了什么”以及语音会话的生命周期，不订阅视觉数据，不直接控制
底盘，也不负责行为优先级、排队或抢占。

## 主要能力

- 支持 ReSpeaker Flex 和讯飞串口模块唤醒并获取声源原始方向角。`wake_angle` 单位为度，
  `header.frame_id=microphone_array`；本项目不应用安装零偏或方向正负标定，
  这些标定只由动作项目在执行转向时应用一次。
- 使用 sherpa-onnx Silero VAD 实时切分语音。
- VAD 与流式 KWS 复用同一份 16 kHz 麦克风数据，不重复打开设备。
- 直接判断 ReSpeaker CDC JSON：`type=vad,state=update,doa_valid=true` 时发布
  `EVT_VOICE_SOUND_SOURCE_DETECTED`，携带原始 DOA；false 不发布，收到有效更新就下发。
  见 [声源事件接入](docs/SOUND_SOURCE_EVENTS.md)。
- KWS 检测56条中文动作词（14个标签），第一次命中通过否定检查后发布，携带会话和语句编号。
  同一句的后续关键词不再发布命令；不同语句可以再次执行相同命令。
- 使用当前配置的 SenseVoice INT8 ONNX 完成整句 ASR，优先精确匹配
  按 [语音识别内容.xlsx](docs/语音识别内容.xlsx) 重建的词库：49 个功能条目中有
  43 个固定路由、4 个参数句式、1个闲聊兜底和1个声源入口。固定词库含 222 条标准词句（核心子集21组），
  每条另有10个受控扩展，复用123条口语/ASR变体，共2565个精确入口。词库和 KWS 命中后
  只发布目录指定的 `EVT_VOICE_*` 特殊事件，不额外发布
  `EVT_VOICE_COMMAND_KNOWN`。所有目录命中均跳过意图模型。
- 正式配置不加载 Model Intent。未命中固定词库及取物/地点句式的文本发布不可执行
  `EVT_VOICE_COMMAND_UNKNOWN`；Model Intent 字段和事件映射保留为兼容协议，不代表正式链路会运行模型。
- 兼容模型路径的 `FETCH/FIND_TOY` 额外经过目标物白名单：只接受 `config/object_targets.yaml` 中
  18 个视觉检测类别。命中时以规范英文类别写入 `slots.object_name` 并发布可执行
  `EVT_VOICE_COMMAND_FETCH`；未命中时写入 `object_name=NONE`，只保留业务大类事件。
- 支持说话人识别、录制注册，以及通过 FastAPI 上传 WAV、VAD 截取后注册声纹。
- 使用 `interaction_id` 和 `utterance_id` 关联一次会话及其中的每句话。
- 支持 pipeline Mock 和直接事件 Mock，无硬件也可联调下游。

正式配置使用 `providers.kws.config.publish_mode=immediate`，首次有效 KWS 命中即取得
同句业务结果，不再要求连续两次命中或整句 ASR 确认。
正式配置启用 `negation_guard_enabled`：独立 KWS 流检测
“不要 / 别 / 不准 / 不许”，
动作命中后等待 `negation_guard_window_sec=0.25` 秒后续音频通过检测，再交给节点处理。
确认窗口按已处理音频计时；录音停止或 VAD 结束不会提前放行，未确认动作交给 ASR。
命中否定词会拦住本句 KWS 和 ASR 动作回退，即使 ASR 漏掉否定前缀也不放行。
“别动 / 不要动 / 不可以 / 别叫 / 不可以吃 / 不可以咬”等完整登记的控制短语
和整句精确命中的责备表达保留；
责备只发布社交反应，不执行句中的“站着”等动作片段。
即时模式中，同句的业务结果使用 KWS 来源。即时事件的 `asr_text` 为空，
`speaker_id=unknown/speaker_status=pending`；VAD 完成后，每句独立核验声纹，
身份事件和文本通过相同的 `interaction_id/utterance_id` 关联。
唤醒身份不会覆盖该句识别结果；ASR 路由的业务事件携带该句实际识别出的身份。
该拦截只覆盖配置的中文否定词，
不代替完整语义理解；漏检、引用句或发布后的改口仍可能误触发，已发动作不会自动撤回。

可选的 `publish_mode=deferred` 使用整句 ASR 仲裁：长文本使用 ASR，普通短指令要求 ASR
词库确认同一事件，精确错写白名单及空 ASR 行为由对应配置控制。两种模式均只发布目录
指定的具体事件；启用逐句声纹时，身份结果与 `speech` 保留独立事件。同句没有 KWS 业务结果时才进入
ASR 词库和意图业务路由（被否定拦截的语句除外）。

可选的 deferred 重复发布通道可显式对 `COME` 启用：连续命中两次，相邻有效命中的音频间隔
在 0.3～1.5 秒内，且同句没有其他命令候选时，第二次命中再经过 250 ms 否定确认后，
立刻发布一次 `EVT_VOICE_COMMAND_COME`，无需等 VAD 或整句 ASR。
例如“过来过来过来过来”在满足上述条件后提前下发一次；后续重复和 ASR 不再下发业务
命令，保留文本和该句独立核验的身份事件。提前发布的命令身份为 `unknown/pending`，
通过同一 `utterance_id` 关联稍后发布的声纹结果。
正式配置未启用该通道；其配置为 `repeat_early_command_keys: [COME]`、`repeat_early_min_hits: 2`、
`repeat_early_min_gap_sec: 0.3`、`repeat_early_max_gap_sec: 1.5`；空列表禁用，
启用时必须保留 `negation_guard_enabled=true`，`immediate` 模式忽略此通道。
计数依据 KWS 标签，因此“回来”等同属 `COME` 的关键词也可累积；重复取物片段、
引用句或后续改口仍可能误触发，此通道不能保证整句语义确认。现场需验收重复呼叫、
否定句和重复参数句的识别及响应时间。

上述长度阈值只决定“已有 KWS 候选能否胜出”，不会自动把所有一字/两字词加入 KWS。
本版 KWS 只保留2～5字的短动作词；“走、来、坐、停”等单字仍可由整句 ASR 精确匹配。
单字不进行同音兜底。否定词使用独立的 `kws_negation_keywords_raw.txt` / `kws_negation_keywords.txt`，
用 `scripts/generate_kws_keywords.py --negation` 生成；不计入业务动作词表。

2026-10-16 产品识别表已对齐：“走”属于跟随，“站好/站着”属于起身，
“不可以咬”属于松口，“小宝贝/乖狗狗/小跟屁虫”属于夸赞。固定同义词在
`config/command_catalog.yaml`，昵称呼叫与待机唤醒仍分开。默认 ReSpeaker 主机唤醒词表包含
“旺财旺财、小火小火、小狗、跟屁虫”，不修改设备固件词表。
“去追球”整句发布独立的 `EVT_VOICE_COMMAND_CHASE_BALL / CMD_CHASE_BALL`，
表格来源行为说明包含追球、随机放球及最多5分钟；实际流程由下游接入。

未被 KWS 提前发布的“去拿球/把球拿来”不依赖意图模型，发布 `EVT_VOICE_COMMAND_FETCH`，
携带 `object_name=dog toy ball`。“去房间/到客厅去”发布
`EVT_VOICE_COMMAND_GO_TO_LOCATION`，携带对应的 `location_id`。单字“去”不再作为即时
KWS 关键词，完整 ASR 句子会先匹配地点句式；独立“去”和“走开”的 WALK 路由已移出本版。
“往前走走/前进/向前走”发布独立 `MOVE_FORWARD`，不复用旧随机走动事件。
地点别名在 `config/location_targets.yaml`；Voice 只识别和发布，
调用方负责地图点位绑定、导航与动作。未知物品/地点仍发布带原始名称的不可执行识别摘要，
不猜测目标类别或坐标。完整字段见 [ROS2_CONTRACT.md](docs/ROS2_CONTRACT.md#带参数的取物与地点命令)。

“去厨房拿狗绳子”发布 `EVT_VOICE_COMMAND_FETCH_FROM_LOCATION`，同时保留地点和物品。
“去找xx”发布 `EVT_VOICE_COMMAND_FIND_PERSON`；只有 `config/person_targets.yaml` 中已配置的
身份才能授权执行，当前仅配置“主人 → owner”；其他人物只发布不可执行摘要。
声纹注册不自动同步寻人目录，下游仍需完成人脸注册和寻人动作接入。
“去拿玩具/去找玩具”发布可执行 FETCH，携带 `object_name=NONE, object_group=toy`，
由下游选择具体玩具。“去门口/去门口拿狗绳”使用 `location_id=entrance`，下游需绑定门口点位。
Flex 唤醒方向超过 600 ms 时仍发布唤醒并开始识别，只标记 `wake_angle_valid=false`，禁止据此转向。
KWS 即时发布仍可能先捕获长句中的短词；需要整句确认的部署使用 deferred 模式。

词库修改入口为 Excel 和 `config/command_catalog_rules.yaml`，生成及校验：

```bash
.venv/bin/python scripts/generate_command_catalog.py
.venv/bin/python scripts/generate_kws_keywords.py
.venv/bin/python scripts/generate_command_catalog.py --check
.venv/bin/python scripts/generate_kws_keywords.py --check
```

生成器保留实际 Excel 行号、原始行为说明、次序列和备注，并校验所有43个功能条目。
YAML 在节点启动时加载，修改后需在正确的 ROS overlay 下重启节点。

## 项目边界

| 项目 | 职责 |
|---|---|
| 本项目 | 唤醒、录音、VAD、KWS、ASR、声纹、意图和语音会话事件 |
| 视觉项目 | 人脸、目标检测和视觉事件 |
| 行为树项目 | 候选行为、优先级、仲裁和抢占 |
| 动作项目 | 跟随、姿态动作和底盘执行 |

下游开发接入请先看 [开发对接文档](docs/DOWNSTREAM_INTEGRATION.md)及
[完整命令映射表](docs/DOWNSTREAM_COMMAND_MAP.csv)。跨项目交接语义见
[docs/HANDOFF.md](docs/HANDOFF.md)，完整 ROS2 消息字段和
Service 参数见 [docs/ROS2_CONTRACT.md](docs/ROS2_CONTRACT.md)，测试执行、日志
字段和报告模板见 [docs/TESTING_LOG_GUIDE.md](docs/TESTING_LOG_GUIDE.md)。

新版按产品表将“叫两声/说话”统一到 BARK；前移、左右转、调速、绕人、四类游戏独立路由。
“自己去玩吧”“这是啥”“翻滚”等旧版表外独立路由不再保留。
动作序列、全局速度、UWB/电量条件、视觉会话保持和任务超时由 Tree/Action 等下游落实。

222 条标准词/句、扩展规则与期望事件见
[docs/COMMAND_CATALOG_TEST_MATRIX.md](docs/COMMAND_CATALOG_TEST_MATRIX.md)。

## 运行环境

- Ubuntu 22.04 / ROS2 Humble
- Python 3.10
- [uv](https://docs.astral.sh/uv/)
- 麦克风及 PortAudio；无法使用 `sounddevice` 时会回退到 `arecord`
- 离线语音唤醒模块；本机按 USB 身份选择 WCH 外置串口接收 AIUI 唤醒事件，
  ListenGo 声卡负责录音
- RK3588 平台及 RKLLM 运行库（仅启用 `intent_llm` 时需要）

Python 依赖由 `pyproject.toml` 和 `uv.lock` 管理：

```bash
cd /home/cat/marsdog_ws/src/MarsDogVoiceInteraction
uv sync --extra dev
```

推理模型不提交到仓库，默认统一存放在项目目录同级的 `../models/`：

| 模块 | 默认路径 |
|---|---|
| VAD | `vad/silero_vad.onnx` |
| KWS | `wakeup/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/` |
| ASR | `asr/sherpa-onnx-paraformer-zh-2024-03-09/` |
| Speaker | `speaker/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx` |
| RKLLM Model Intent | `llm/qwen2_5_5b_rk3588_260903_w8a8.rkllm` |

当前 Model Intent 文件 SHA-256 为
`3c316cede8dcc40c6f019f7a2403f56c2d567eeacc29f410b656eb02981ca0b1`；测试和部署
应同时核对文件名与校验值，避免板子仍加载旧模型。

部署到其他目录时，建议保持“项目目录与 `models/` 同级”的相对布局；如果目录结构
不同，只需修改所选配置（默认为 `config/voice.respeaker.yaml`）中对应的相对路径。

`storage.root` 下保存的是运行期声纹注册表、VAD 截取后的 WAV 和用户声纹向量，
属于设备本地的生物特征数据，已通过 `.gitignore` 排除，不应提交到版本库。程序会
在首次运行或注册时自动创建所需目录和注册表。

## 构建

当前开发副本位于 ROS2 工作空间：

```text
/home/cat/marsdog_ws/src/MarsDogVoiceInteraction
```

在工作空间中构建：

```bash
source /opt/ros/humble/setup.bash
cd /home/cat/marsdog_ws
colcon build --packages-select marsdog_voice_interaction --symlink-install
source install/local_setup.bash
```

必须经过 colcon 构建后才能生成并使用自定义的 `VoiceTask.srv`。

## 启动

### ROS2 Launch（推荐）

```bash
source /opt/ros/humble/setup.bash
source /home/cat/marsdog_ws/install/local_setup.bash
ros2 pkg prefix marsdog_voice_interaction
# 应输出 /home/cat/marsdog_ws/install/marsdog_voice_interaction
MARSDOG_PYTHON=/home/cat/marsdog_ws/src/MarsDogVoiceInteraction/.venv/bin/python \
ros2 launch marsdog_voice_interaction voice.launch.py
```

指定另一份配置：

```bash
ros2 launch marsdog_voice_interaction voice.launch.py \
  config_path:=/absolute/path/to/voice.yaml
```

`config_path` 只选择配置，不选择 Python 代码；进入 `marsdog_ws` 目录也不会切换
ROS 环境。如果终端已加载旧的 `/home/cat/ros2_ws/install`，必须最后再 source
当前 `marsdog_ws/install/local_setup.bash`。若启动栈仍指向 `ros2_ws/install`，
或报 `publish_mode must be 'deferred'`，先检查上述包路径，不要改回旧发布模式。

### 直接运行源码入口

适合开发调试，但调用 `/perception/voice/task` 前仍需 source 已构建的工作空间：

```bash
cd /home/cat/marsdog_ws/src/MarsDogVoiceInteraction
source /opt/ros/humble/setup.bash
source /home/cat/marsdog_ws/install/local_setup.bash
uv run marsdog-voice-interaction \
  --ros-args -p config_path:="$PWD/config/voice.respeaker.yaml"
```

## 配置

默认配置文件为 [`config/voice.respeaker.yaml`](config/voice.respeaker.yaml)，用于
ReSpeaker Flex 唤醒和录音。原 [`config/voice.yaml`](config/voice.yaml) 保留讯飞 /
ListenGo 配置，需要使用旧设备时通过 `config_path` 显式指定。

| 配置段 | 作用 |
|---|---|
| `logging` | 日志级别和输出目录 |
| `mock` | Mock 开关、模式和事件间隔 |
| `storage.root` | 声纹注册表、样本及临时数据目录 |
| `command_lexicon` | 完整产品词库、21 组核心子集及每标准词/句 10 个受控精确匹配扩展 |
| `parameterized_commands` | 取物、地点、找人和地点取物组合句式；地图及身份绑定由调用方完成 |
| `speaker_api` | 声纹上传 API 的开关、监听地址、端口和大小限制 |
| `topics` | ROS2 Topic 和 Service 名称 |
| `interaction.idle_timeout_sec` | 非空 ASR 或即时 KWS 命令后的空闲超时；`<=0` 禁用。正式配置为 `30` 秒 |
| `interaction.max_duration_sec` | 单次唤醒会话总时长上限；`<=0` 禁用。正式配置为 `0`，由空闲超时或 `stop_listening` 结束 |
| `interaction.hold_max_lease_sec` | 外部会话保持租约的单次最长秒数 |
| `interaction.refresh_on_any_speech` | 测试专用：`true` 时 VAD 检测到语音即刷新空闲计时器；生产为 `false` 时要求 ASR 返回非空文本，不要求语义接受。见 `docs/TESTING_LOG_GUIDE.md` 的「测试模式」小节 |
| `providers.wakeup` | 讯飞串口和唤醒事件类型 |
| `providers.audio` | 麦克风、VAD 阈值、语音时长和预录缓存 |
| `providers.kws` | KWS 模型、显式关键词、检测阈值及 immediate/deferred 发布模式 |
| `providers.asr` | ASR 模型、语言和 ITN |
| `providers.speaker` | 声纹模型及匹配阈值 |
| `providers.intent_rule` | 规则意图回退 |
| `providers.intent_llm` | RKLLM 模型和生成参数 |

配置中的文件和目录统一使用相对路径，并以当前 YAML 文件所在目录为基准解析，
不依赖启动命令的工作目录。例如 `storage.root: ../data` 指向项目的 `data/`，
`vad_model: ../../models/vad/silero_vad.onnx` 指向项目同级的模型目录。串口设备
`/dev/serial/by-id/...` 和 ROS2 Topic/Service 名称不是项目文件路径，仍使用系统设备名和
ROS2 绝对名称。

与收音质量直接相关的参数：

| 参数 | 当前值 | 说明 |
|---|---:|---|
| `vad_threshold` | `0.5` | 越低越容易检测轻声，也越容易受噪声影响 |
| `min_speech_dur` | `0.25` 秒 | 低于此时长的声音不作为有效语音 |
| `min_silence_dur` | `0.5` 秒 | 句尾持续静音达到该时长后结束切分 |
| `pre_roll_sec` | `0.3` 秒 | 补回 VAD 触发前的音频，防止漏掉句首 |
| `max_duration_sec` | `8.0` 秒 | 尚未检测到语音时的最长等待时长 |
| `max_speech_duration_sec` | `8.0` 秒 | 检测到语音后独立计时的最长时长 |
| `read_timeout_sec` | `2.0` 秒 | 单个录音后端连续无音频数据的超时 |
| `continuous_capture` | `true` | 会话内保持麦克风打开，ASR/逐句声纹/语义处理期间继续缓存音频 |
| `capture_buffer_sec` | `6.0` 秒 | 待处理音频缓存容量及最大保留时间；超限丢弃受影响的句子 |

持续采集的缓冲区保存音频，KWS 在逐句消费时运行。即时模式下，上一句的整句识别在
后台串行执行，下一句继续进行 VAD 和 KWS。整句识别等待音频的容量与最长保留时间
由 `capture_buffer_sec` 限定；超限或过期记录 `speech_processing_queue result=discarded`。
重新唤醒或会话结束时丢弃旧任务结果。录音缺失时等待静音边界后恢复采集。
VAD 检测灵敏度参数保持原值；采样率限定为 16 kHz，`num_threads` 显式传给
实时和上传 VAD。`continuous_capture: false` 可恢复逐句开关设备的兼容路径。

调节 VAD 阈值前，应先检查系统麦克风输入设备和硬件增益。阈值过低会把风扇、
碰撞声等环境噪声误判为语音。

### VAD → ASR 调试音频

正式配置可通过顶层 `audio_debug` 开关保存同一个 `utterance_id` 的三阶段证据：

```yaml
audio_debug:
  enabled: true
  output_dir: /tmp/voice_debug
  save_raw_capture: true
  save_vad_segment: true
  save_asr_input: true
  compare_asr: false
  debug_disable_extra_pre_roll: false
  # 保留的 utterance 目录数上限，超出后按时间删除最旧的；<= 0 表示不清理。
  max_utterances: 200
```

目录内的 `01_raw_capture.wav` 是连续麦克风输入，`02_vad_segment.wav` 是 VAD
原始 segment（多段时使用原始采集间隔保留真实停顿），`03_asr_input.wav` 是紧邻
当前 ASR recognizer `accept_waveform()` 前保存的最终 ndarray。三者均为单声道 IEEE float32
WAV。日志 `audio_debug/vad_boundary/asr_boundary/vad_join` 分别记录幅值和格式、VAD
起止、实际 pre-roll、原始与拼接后停顿。

只切换 `debug_disable_extra_pre_roll` 可执行 A/B：`true` 为 VAD 原始 segment 直接送
ASR，`false` 为额外 `pre_roll_sec` 加 VAD segment；其他 VAD/ASR 参数不变。
`compare_asr: true` 会在正常识别外再用同一模型识别三份 WAV，开销较大，默认关闭。

`max_utterances` 限定保留的 utterance 目录数，超出后每次落盘最多删除 16 个最旧目录
（分批删除，避免一次删除造成存储抖动）；设为 `0` 表示不清理。被取消的采集（会话超时、
用户打断）不写调试音频——取消后采集结果本就不会被采用，落盘只会拖长采集线程的退出。

调试结束后应将 `audio_debug.enabled` 设为 `false`；关闭时不创建目录、WAV 或执行对照
识别。

## 命令行直接录入声纹

停止语音节点、独立声纹服务和浏览器录音后，在项目根目录执行：

```bash
.venv/bin/python scripts/record_speaker.py
```

默认向主人 `owner` 追加 3 条样本，每条录音 6 秒。按终端提示，准备好后按回车，
再朗读显示的句子。脚本直接使用 `config/voice.respeaker.yaml` 中的 ALSA
`arecord_device`，无需网页、HTTP 服务或 ROS。所有录音经过 VAD 和声纹身份校验后
再批量保存到同一个 `storage.root`，已有样本不会被覆盖；每人总计最多 5 条。
录入完成后重新启动语音节点，即可加载新样本。

```bash
# 只追加一条，录制 8 秒
.venv/bin/python scripts/record_speaker.py --shots 1 --seconds 8

# 指定家人身份
.venv/bin/python scripts/record_speaker.py --speaker family_member_1
```

可通过 `--config` 指定配置、`--device plughw:CARD=CDC,DEV=0` 指定 ALSA 设备，
或用 `--no-prompt` 连续录制。麦克风被占用、录音超时、全零或缺少有效语音时会报错，
不会改用其他麦克风，也不会自动停止正在运行的服务。

## 单独启动声纹网页

声纹网页可以独立运行，无需启动 ROS 语音节点。独立入口复用现有网页、VAD、
声纹模型和样本管理接口，只处理浏览器上传的音频，不打开麦克风或唤醒串口，
不加载 ASR、KWS 和意图模型。

在项目根目录运行（使用与语音节点相同的配置）：

```bash
.venv/bin/python -m marsdog_voice_interaction.speaker_service \
  --config config/voice.respeaker.yaml
```

打开 `http://localhost:8091/speaker-enrollment`。端口和监听地址默认读取
`speaker_api`；需要时可以加 `--host 127.0.0.1 --port 8092` 覆盖。
`speaker_api.enabled` 控制语音节点内嵌服务，显式启动独立入口时会启用 HTTP 服务。

构建并 source 工作空间后，也可以从项目根目录运行：

```bash
ros2 run marsdog_voice_interaction speaker_service --config config/voice.respeaker.yaml
```

使用机器人同一只 ReSpeaker 录入时，按以下顺序操作：

1. 在启动语音节点的终端按 `Ctrl+C`，释放麦克风和内嵌服务的 8091 端口。
2. 启动上述独立声纹服务，在网页允许麦克风权限并录入、保存样本。
3. 录入完成后，在独立服务终端按 `Ctrl+C`。
4. 重新启动语音节点，它会从同一个 `storage.root` 加载保存的声纹。

相对模型路径和 `storage.root` 都按 YAML 所在目录解析；当前配置保存到本项目
`data/`，不会自动导入其他检出目录的历史声纹。独立服务本身不采集麦克风，
但仍在运行的语音节点会继续占用 ReSpeaker，仅换 HTTP 端口不能解决录音冲突。
同一声纹目录当前只支持一个写入进程；运行中的语音节点也不会自动刷新其他进程
写入的模板，因此使用上述先录入、再启动语音节点的流程。

## 声纹上传 API

正式配置随语音节点启动 FastAPI。本机打开
`http://localhost:8091/speaker-enrollment` 可选择主人或家人槽位、用浏览器麦克风录音、
调用服务端 Silero VAD 获取建议范围、手动调整起止点并试听，最后保存为声纹样本。
页面和 API 同时启动，不需要单独启动前端。页面最长录制 60 秒；分析接口
`POST /api/v1/recordings/vad` 只返回语音时间范围，不保存音频。点击保存后仍走原有
`POST /api/v1/speakers/{name}/samples`，再次执行 VAD、声纹提取和身份冲突校验。
浏览器需允许麦克风权限；从其他设备打开时应使用 HTTPS 或把服务转发到浏览器本机的
`localhost`。交互文档在 `http://localhost:8091/docs`。接口接收
`multipart/form-data`，无需把音频转换成 Base64：

```bash
curl -X POST http://127.0.0.1:8091/api/v1/speakers/owner/samples \
  -F 'audio=@/path/to/speaker.wav;type=audio/wav'

# 一次原子新增多个样本（重复 audios 字段，最多 5 个）
curl -X POST http://127.0.0.1:8091/api/v1/speakers/owner/samples/batch \
  -F 'audios=@/path/to/owner-1.wav;type=audio/wav' \
  -F 'audios=@/path/to/owner-2.wav;type=audio/wav'
```

上传文件必须是未压缩的 16-bit PCM WAV，可为 1～8 声道、8～96 kHz。节点会转为
16 kHz 单声道，用独立的 Silero VAD 去除首尾静音并拼接有效语音段，再提取声纹。
无论源文件是 8、16、44.1 或 48 kHz，成功保存的 WAV 都统一为 16 kHz、单声道、
16-bit PCM；响应同时返回 `source_sample_rate`、`stored_sample_rate=16000`、
`stored_channels=1` 和 `stored_sample_width_bits=16`。
`name` 不是自由输入姓名，只能从 `owner`、`family_member_1`、
`family_member_2`、`family_member_3`、`family_member_4` 中选择；Swagger `/docs`
会显示该枚举。结果按以下结构保存在 `storage.root`，同一身份上传会新增序号并重新
计算 `centroid.npy`：

```text
data/speakers/owner/
├── 001.wav
├── 001.npy
└── centroid.npy
```

存储根目录只能由所选配置的 `storage.root` 决定。HTTP 请求中没有目录
参数，客户端传入的路径字段不会改变落盘位置。整个 `data/speakers` 最多保存 5 个
身份槽位，对应 1 个主人和 4 个家人。每个身份最多保存 5 个声纹样本，同一身份第
6 次上传返回 HTTP `409`，不会生成 `006.wav/006.npy`。

新增或替换样本时，会把新 embedding 与其他身份的每个已有样本做余弦相似度比较。
达到 `speaker_api.cross_identity_similarity_threshold`（当前为 `0.75`）时返回 HTTP
`409` 和 `speaker_identity_conflict`，并给出 `conflicting_speaker`、`similarity` 与
`similarity_threshold`；例如主人已注册后，同一个人的声音不能再注册为家人。同一身份
内部允许继续追加同一个人的多个样本。该阈值依赖模型和采集环境，上板验收后可调，
不应把 `0.75` 当作跨设备通用结论。

管理接口：

```bash
# 查询人员和样本数
curl http://127.0.0.1:8091/api/v1/speakers

# 给 owner 新增一条样本
curl -X POST http://127.0.0.1:8091/api/v1/speakers/owner/samples \
  -F 'audio=@/path/to/new-owner.wav;type=audio/wav'

# 查询 owner 的具体样本及稳定 sample_id
curl http://127.0.0.1:8091/api/v1/speakers/owner/samples
curl http://127.0.0.1:8091/api/v1/speakers/owner/samples/1

# 下载 owner 的第 1 条 VAD 后 WAV；不提供 embedding 下载接口
curl -o owner-001.wav \
  http://127.0.0.1:8091/api/v1/speakers/owner/samples/1/audio

# 用新音频替换 family_member_1 的第 2 条样本
curl -X PUT \
  http://127.0.0.1:8091/api/v1/speakers/family_member_1/samples/2 \
  -F 'audio=@/path/to/replacement.wav;type=audio/wav'

# 只删除 owner 的第 1 条样本
curl -X DELETE \
  http://127.0.0.1:8091/api/v1/speakers/owner/samples/1

# 删除 owner 的全部样本
curl -X DELETE \
  http://127.0.0.1:8091/api/v1/speakers/owner/samples

# 删除全部固定身份声纹；必须显式确认，避免误操作
curl -X DELETE \
  'http://127.0.0.1:8091/api/v1/speakers?confirm=true'
```

样本编号是稳定的 `1～5`：删除 `001` 不会把 `002` 重命名为 `001`；下次新增会复用
最小空闲编号。替换和删除都会重新计算该身份的 `centroid.npy` 并同步当前进程的声纹
检索索引。删除最后一条样本时，该身份目录和注册表记录一并移除，身份槽位重新可用。
新增、批量新增、替换都执行相同的 WAV、VAD、有效语音、16 kHz 归一化、embedding
和跨身份冲突校验，失败时保留原样本。批量新增先校验全部文件，任一文件失败时整批
不落盘。删除单个身份使用样本集合 DELETE；删除全部身份还必须传 `confirm=true`。
旧版顶层上传、身份改名和 `DELETE /api/v1/speakers/{name}` 仍保持移除。

运行时声纹事件按身份固定路由：`owner` 发布 `EVT_VOICE_MASTER_ID`，任一
`family_member_1`～`family_member_4` 发布 `EVT_VOICE_FOLK_ID`，`unknown` 或
未匹配人员发布 `EVT_VOICE_UNMASTER_ID`。身份事件仍由行为树等下游
通过 `/perception/audio_event` 消费，不由 Voice 直接调用动作系统。

当前配置监听 `0.0.0.0:8091`，局域网内直接访问，不包含 Token 或其他身份验证：

```bash
curl -F 'audio=@/path/to/speaker.wav;type=audio/wav' \
  http://DOG_IP:8091/api/v1/speakers/owner/samples
```

接口会直接暴露声纹样本的上传、查询、下载、替换和删除能力，目前只应运行在可信开发
局域网。
认证模块已移除，后续生产认证方案需要另行设计和接入。

健康检查为 `GET /health`。完整状态码和字段见
[docs/ROS2_CONTRACT.md](docs/ROS2_CONTRACT.md)。

## ROS2 接口

| 方向 | 名称 | 类型 | 说明 |
|---|---|---|---|
| 发布 | `/perception/audio_event` | `std_msgs/msg/String` | JSON 语音、身份、指令及会话状态事件 |
| 发布 | `/perception/voice/enrollment_event` | `std_msgs/msg/String` | JSON 声纹注册进度事件 |
| 提供 | `/perception/voice/task` | `marsdog_voice_interaction/srv/VoiceTask` | 监听和声纹管理任务 |

支持的 `task_type`：

- `start_listening`、`stop_listening`
- `hold_interaction`、`release_interaction_hold`、`get_interaction_state`
- `start_speaker_enrollment`、`cancel_speaker_enrollment`
- `verify_speaker`

接口发现与监听：

```bash
ros2 topic info -v /perception/audio_event
ros2 topic echo /perception/audio_event
ros2 topic echo /perception/voice/enrollment_event
ros2 service type /perception/voice/task
ros2 interface show marsdog_voice_interaction/srv/VoiceTask
```

手动开始和停止监听：

```bash
ros2 service call /perception/voice/task \
  marsdog_voice_interaction/srv/VoiceTask \
  "{task_id: manual-start, task_type: start_listening, params_json: '{}'}"

ros2 service call /perception/voice/task \
  marsdog_voice_interaction/srv/VoiceTask \
  "{task_id: manual-stop, task_type: stop_listening, params_json: '{}'}"
```

`start_listening` 成功时返回本次不可变的 `interaction_id`。调用方若要确认
仍是同一会话，可传 `expected_interaction_id`；会话已结束时不会用旧 ID 重新
创建会话。

行为树在声源转向或靠近唤醒者期间，可以使用有限租约暂停空闲超时：

```bash
ros2 service call /perception/voice/task \
  marsdog_voice_interaction/srv/VoiceTask \
  "{task_id: wake-hold, task_type: hold_interaction, params_json: \
  '{\"interaction_id\":\"SESSION_ID\",\"hold_token\":\"wake-engagement:SESSION_ID\",\"lease_sec\":6.0,\"reason\":\"wake_target_approach\"}'}"

ros2 service call /perception/voice/task \
  marsdog_voice_interaction/srv/VoiceTask \
  "{task_id: wake-release, task_type: release_interaction_hold, params_json: \
  '{\"interaction_id\":\"SESSION_ID\",\"hold_token\":\"wake-engagement:SESSION_ID\",\"reset_idle_timer\":true}'}"
```

同一 `hold_token` 重复申请是幂等续租。租约只暂停空闲终止，不暂停录音、KWS
或 STOP；到达目标后用 `reset_idle_timer=true` 释放，会从释放时重新等待当前配置的
`idle_timeout_sec`（正式配置为 30 秒）。

## Mock 联调

仓库提供 [`config/voice.mock.yaml`](config/voice.mock.yaml)，无需串口、麦克风和
模型即可直接发布模拟事件：

```bash
source /opt/ros/humble/setup.bash
source /home/cat/ros2_ws/install/setup.bash
ros2 launch marsdog_voice_interaction voice.launch.py \
  config_path:=/home/cat/xbb/MarsDogVoiceInteraction/config/voice.mock.yaml
```

Mock 有两种模式：

- `mock.mode: event`：绕过全部上游 Provider，按 `WAKEUP → 会话事件 → idle`
  生成语义完整的同 `interaction_id` 会话，适合测试行为树等下游消费者。
- `mock.mode: pipeline`：使用 Mock Wakeup、VAD、ASR 和 Speaker 走完整节点流程，
  适合验证状态机和 Provider 编排。使用
  [`config/voice.pipeline.mock.yaml`](config/voice.pipeline.mock.yaml)。

`voice.mock.yaml` 默认使用 `event` 模式。每次启动必须检查 `runtime_start` 中的
`providers`；模式名称表示所选配置，实际 Provider 列表才是本次真机/Mock 判定依据。

## 日志和测试取证

节点在终端和独立文件中输出日志。每次运行的文件名为
`voice_interaction_YYYYMMDD_HHMMSS_<pid>.log`，准确路径记录在启动时的
`runtime_start.log_file`。`logging.level/dir/console/file/event_trace` 由配置文件
控制，也可以在 launch 时覆盖级别和目录：

```bash
ros2 launch marsdog_voice_interaction voice.launch.py \
  config_path:=/home/cat/xbb/MarsDogVoiceInteraction/config/voice.mock.yaml \
  log_level:=DEBUG \
  log_dir:=/tmp/marsdog_voice_qa/VOICE-MOCK-001
```

测试证据使用单行 JSON `VOICE_TRACE`。它覆盖实际 Provider、会话起止、VAD/KWS/
ASR/声纹/意图阶段耗时、每次 Topic 发布、VoiceTask 返回和注册进度；通过
`interaction_id` 与 `utterance_id` 串联一条完整时间线：

```bash
rg 'VOICE_TRACE' /tmp/marsdog_voice_qa/VOICE-MOCK-001
```

完整字段、功能判定、证据包内容和测试报告模板见
[docs/TESTING_LOG_GUIDE.md](docs/TESTING_LOG_GUIDE.md)。

## 测试

```bash
cd /home/cat/xbb/MarsDogVoiceInteraction
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q marsdog_voice_interaction tests
```

完成代码或接口修改后，建议再执行一次 ROS2 构建：

```bash
source /opt/ros/humble/setup.bash
cd /home/cat/ros2_ws
colcon build --packages-select marsdog_voice_interaction --symlink-install
```

## 常见问题

### 说话需要很大声，或识别结果缺少开头

1. 检查系统默认麦克风是否正确，以及输入增益是否过低。
2. 观察启动日志中的录音后端、VAD 阈值和检测时长。
3. 适度降低 `vad_threshold`，但不要一次降得过多。
4. 确认 `pre_roll_sec` 大于 `0`，用于保留 VAD 触发前的句首。

### 找不到 `/perception/voice/task` 或 `VoiceTask` 类型

自定义 Service 尚未生成，重新执行 colcon 构建并 source 工作空间：

```bash
source /opt/ros/humble/setup.bash
cd /home/cat/ros2_ws
colcon build --packages-select marsdog_voice_interaction --symlink-install
source install/setup.bash
```

### 启动的不是当前源码版本

先确认 ROS2 实际解析到的安装前缀：

```bash
ros2 pkg prefix --share marsdog_voice_interaction
```

预期结果为：

```text
/home/cat/ros2_ws/install/marsdog_voice_interaction/share/marsdog_voice_interaction
```

若结果来自其他工作空间，请打开一个干净终端，只 source ROS2 Humble 和当前
`/home/cat/ros2_ws/install/setup.bash`。

### 串口无法打开

确认设备存在、端口配置正确，并检查当前用户是否有串口访问权限：

```bash
ls -l /dev/serial/by-id/
```

默认 Flex 配置按 `303a:4013` 和序列号 `FLEX-ESP32S3-0001` 选择 CDC 串口，
详见 [Flex 接入说明](docs/RESPEAKER_INTEGRATION.md)。若已接入 Flex，日志却仍出现
`L6Microphone` 或 `WakeupXFYunSerialProvider`，请检查实际加载的 `config_path`。

以下说明适用于旧讯飞 / ListenGo 配置：`config/voice.yaml` 使用 `port: auto`，按 `usb_vid: 0x1a86`、
`usb_pid: 0x55d4` 选择外置 WCH 唤醒串口。启动及每次断线重连都会重新枚举，
设备改成其他 `ttyACM` 编号或更换同型号串口桥后无需修改路径。匹配不到设备时每
2 秒重试；匹配多个设备时记录候选端口并等待，不自动任选。多设备场景可设置
`serial_number: "0004"`，或将 `port` 设为明确的 `/dev/serial/by-id/...` 路径。
显式路径不会回退到其他串口；同一设备的 by-id 路径本身也支持重连后编号改变。

现场抓包确认外置 WCH 端口发送 AIUI 唤醒数据；ListenGo 内置 CDC 串口未输出
唤醒数据，不能仅凭它与声卡属于同一设备就将其作为唤醒口。串口读写异常（含
USB 移除时的底层 I/O 错误）会结束旧 reader，由 provider 定时重新选择并连接。
自动选择解决端口定位与恢复，不修复 USB Hub、供电或线缆导致的掉线；录音声卡
使用独立的 `providers.audio` 配置和重连机制。

`providers.wakeup.config.protocol` 支持 `aiui`、`json` 和默认的 `auto`。
本机配置为 `aiui`，按[讯飞官方串口协议](https://doc.iflyos.cn/aiui/sdk/smart_doc/serial_port_sdk.html)
校验 `A5 01` 二进制帧、回复相同消息 ID 的 ACK、解压 GZIP JSON（同时兼容本机固件的
未压缩 JSON 载荷），再接收
`type=aiui_event`、`content.eventType=4` 的唤醒事件。重发的同一事件会再次确认但不重复投递。
旧的纯 JSON 串口输出可继续使用 `json` 或 `auto`，不会向纯 JSON 通道发送二进制 ACK。
日志 `AIUI handshake acknowledged` 表示握手已应答；仍需用实际唤醒词验证事件和方向角。

### `sounddevice` 无法打开麦克风

节点会记录 PortAudio 错误并尝试回退到 `arecord`。可先用系统工具确认设备：

```bash
arecord -l
```

如需固定输入设备，在 `providers.audio.config.device` 中配置设备名称或索引。

`device` 是 `sounddevice` 的设备名称或索引，`arecord_device` 是回退录音使用的
ALSA PCM 标识，二者可能不同。未配置 `arecord_device` 时沿用 `device`。
本机 ListenGo 使用以下配置，避免将显示名称传给 `arecord` 导致 `Unknown PCM`：

```yaml
providers:
  audio:
    config:
      device: "ListenGo Circular 6-Microphone"
      arecord_device: "plughw:CARD=L6Microphone,DEV=0"
      reconnect_interval_sec: 2.0
```

`CARD=L6Microphone` 来自 `arecord -l` 的声卡标识，不依赖 `card 3`、`card 4`
等数字编号。若日志先出现 `sounddevice produced no audio`，再出现无法打开设备，
应同时检查 `dmesg --ctime` 中的麦克风 USB 断开重连记录。连续录音配置
`reconnect_interval_sec: 2.0` 后，设备打开失败、被占用或中途掉线时，每 2 秒重试
同一个 ALSA 设备；设备恢复后无需为此重启节点。未配置本项或设为 0 时不自动重试。
重连时清空待处理音频和唤醒音频历史，丢弃断续命令，等待 VAD 静音边界后再识别。
日志 `Microphone capture ready backend=...` 表示对应后端已实际读到音频；
`Voice node ready` 只表示 ROS 接口已启动。设备离线期间仍无法录音。

### VAD 取消收音超时

会话静默超时时，节点会取消当前 VAD 收音。`sounddevice` 路径只读取
`read_available` 已确认就绪的帧，并由 `vad-capture` worker 独占
PortAudio 流的关闭，避免取消线程与 worker 同时清理流。取消后的调试音频落盘会被跳过，
收尾只剩流关闭这一项不可取消的工作。

节点在发布 `EVT_STATE_CHANGED idle` 之后才执行取消，因此唤醒轮询不会被取消等待推迟。
如果仍出现 `VAD capture worker did not stop`，错误日志中的 `backend`、`phase` 和
`worker_age_sec` 分别用于确认采音后端、卡住阶段和线程存活时间；紧随其后的
`VAD capture worker Python stack` 是该线程的**当前调用栈采样**，不是异常回溯，用于判断
它停在采集循环还是收尾路径。

超时后残留的 worker 会被从采集状态中分离（detach）并登记为孤儿，不再阻止下一轮收音；
已退出的孤儿会在下一次 `start_capture()` / `cancel_capture()` 时清理，不会在整个进程
生命周期内累积。`start_capture()` 返回 `False` 时节点会打 `VAD capture refused to start`
并把本次 `stage_start` 记为 `result="ignored"`，而不是继续上报 `result="started"`；该
`utterance_id` 随即作废，不会被用来给别的采集结果命名。

唤醒在采集进行中到达时，节点会先取消这次采集再开新的：唤醒意味着上一句话的会话已被
替换，而 `start_capture()` 在旧 worker 仍持有设备时会拒绝启动——若不先取消，旧采集会
继续跑完，其内容（包含刚说的唤醒词本身）会被当作新 utterance 走完整条识别链路，并可能
额外触发一次动作事件。

采集结果自带其产生时所属的 `utterance_id`（由 `vad-capture` worker 在退出前写入）。
节点只在结果未携带 ID 时才用当前跟踪的 ID 补位，不会覆盖，因此「启动被拒」这类
节点侧与采集侧 ID 不同步的情况下，日志、三阶段调试音频和下游事件仍然指向同一条语音。

## 目录结构

```text
MarsDogVoiceInteraction/
├── config/                         # 运行配置、完整产品词库和 KWS 关键词
├── data/                           # 声纹注册表与样本
├── docs/                           # 交接说明、迁移记录和 ROS2 契约
├── launch/                         # ROS2 launch 文件
├── lib/                            # RKLLM 运行库
├── marsdog_voice_interaction/
│   ├── adapters/                   # RKLLM、讯飞串口底层适配
│   ├── core/                       # 会话状态机、KWS 候选跟踪和声纹注册管理
│   ├── messages/                   # 事件结构、事件类型和意图协议
│   ├── nodes/                      # ROS2 主节点
│   ├── providers/                  # Wakeup/VAD/KWS/ASR/Speaker/Intent
│   └── utils/                      # 配置、日志、时间和 ROS 入口工具
├── srv/VoiceTask.srv               # 语音任务 Service
└── tests/                          # 契约、KWS 和会话恢复测试
```

## 开发约束

- `EVT_VOICE_WAKEUP` 到终止 `EVT_STATE_CHANGED` 必须保持同一个
  `interaction_id`。
- `EVT_VOICE_WAKEUP` 只表示硬件或 pipeline Mock 唤醒；Model Intent 呼名发布
  `EVT_VOICE_CALL_NAME`，昵称词库发布 `EVT_VOICE_COMMAND_CALL_NAME`。两类呼名均为
  社交语义，`should_trigger_behavior_tree=false`，不得作为硬件唤醒或通用动作指令。
- 词库 `PRAISE/SCOLD` 发布 `EVT_VOICE_COMMAND_PRAISE/SCOLD`，保持
  `is_executable=false`，但以 `dispatch_role=social_reaction`、
  `should_trigger_behavior_tree=true` 授权 Tree 生成一次性社交反应。
- 每句话创建新的 `utterance_id`；同句话的 KWS、声纹、speech 和最终路由结果共享
  该 ID。
- 非空 ASR 文本和已经发布的即时 KWS 命令刷新会话活动时间。
  纯 VAD、空 ASR 和尚未发布的候选保持原计时。
- 会话静默时长使用单调时钟计算，不受 NTP、RTC 或人工调整系统时间影响。
- 正式配置的空闲超时为 30 秒：最后一次有效活动后静默超过该时长即结束会话；总时长
  上限为 0（不设硬上限），也可用 `stop_listening` 主动结束。若配置正数，总时长上限
  仍不能被活动或 hold 租约延长。
- 活跃 VAD 采集不能被会话静默超时截断。
- 新增确定性命令时应同步修改 `command_catalog.yaml`、事件类型、测试、ROS2 契约
  以及下游行为树和 Action 行为映射；Voice 不直接调用动作系统。
- 下游应按 `interaction_id + utterance_id + event_type` 做幂等保护。

## 延伸文档

- [下游开发对接文档](docs/DOWNSTREAM_INTEGRATION.md)
- [下游命令映射表 CSV](docs/DOWNSTREAM_COMMAND_MAP.csv)
- [2026 年 10 月 13 日识别改动说明](docs/RECOGNITION_CHANGES_2026-10-13.md)
- [项目交接与上下游语义](docs/HANDOFF.md)
- [ROS2 消息和 Service 完整契约](docs/ROS2_CONTRACT.md)
- [旧项目迁移说明](docs/MIGRATION.md)
