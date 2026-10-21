# ReSpeaker Flex 接入与验收

适用：文档《ReSpeaker Flex 麦克风使用与修改原理(1).md》中的 XIAO ESP32S3
自定义应用固件 0.5.x。不是所有出厂 Flex 固件都提供这套 CDC 协议。

## 配置与启动

`config/voice.respeaker.yaml` 是完整配置，现为 ROS2 launch 和源码入口的默认配置。
原 `voice.yaml` 保留讯飞 / ListenGo 配置，可通过 `config_path` 显式选择。
新配置复用现有 Silero VAD、Sherpa KWS/ASR、声纹与意图路由。设备端普通 KWS 中的
“这儿／这里这里／到这来”发布独立通知；其他普通 keyword 不路由成动作或唤醒。
不使用设备通用声音活动 vad 替换主机人声 VAD。

2026-10-15 本机只读枚举结果：

| 接口 | 实测标识 |
|---|---|
| 应用 USB | `303a:4013` |
| CDC | `/dev/ttyACM0`，序列号 `FLEX-ESP32S3-0001` |
| PortAudio 输入名称 | `ReSpeaker Flex UAC I/O + CDC` |
| ALSA 录音 PCM | `plughw:CARD=CDC,DEV=0` |
| 音频 | 16000 Hz、单声道、16-bit PCM |

串口使用 USB 身份自动发现，不固定 ttyACM 编号。换设备或换主机后核对序列号和
ALSA CARD 名称；多块同型号设备必须明确选择，不能依赖默认声卡。

先检查接口（不打开串口、不录音）：

```bash
lsusb
arecord -l
ls -l /dev/serial/by-id
fuser /dev/ttyACM0
```

在项目根目录运行不发布 ROS 消息的诊断：

```bash
# 只测录音，不碰 CDC；不保存 PCM
.venv/bin/python scripts/check_respeaker.py --audio-only --seconds 10
# 验证备用 arecord 路径
.venv/bin/python scripts/check_respeaker.py --audio-only --arecord --seconds 10
# 关闭占用 CDC 的监视器后，验证 ping/pong、持续录音及真人唤醒事件
.venv/bin/python scripts/check_respeaker.py --seconds 30
```

完整检查会拒绝打开已被 fuser 检出占用的串口。串口不能同时被 doa_watch、监视器
和 Voice 使用。诊断只发送 ping，不修改词表、识别门槛或 NVS，不播放声音、不启动 ROS。
退出码 0 只表示收到音频且（若检查 CDC）收到 pong，不表示真人唤醒识别通过。

编译 Voice 后启动（默认使用 Flex 配置）：

```bash
cd /home/cat/marsdog_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select marsdog_voice_interaction --symlink-install
source /home/cat/ros2_ws/install/setup.bash
source /home/cat/marsdog_ws/install/setup.bash
MARSDOG_PYTHON=/home/cat/marsdog_ws/src/MarsDogVoiceInteraction/.venv/bin/python \
ros2 launch marsdog_voice_interaction voice.launch.py
```

模型路径沿用现有配置，从 YAML 所在目录解析。本次实现不自动启动或重启节点。
显式指定 MARSDOG_PYTHON 可避免从工作区根目录启动时遗漏源码目录中的 .venv。
有多个 Voice checkout 时，最后 source 本次修改所在的 marsdog_ws，不能让旧 ros2_ws
中的另一份 Voice 覆盖它。

## 代码与数据链路

1. `ReSpeakerSerialReader` 的后台线程负责发现、DTR=True / RTS=False、等待稳定、
   ping/pong、JSON Lines 分帧及重连。`poll_event()` 不执行串口等待。
2. 每 10 秒 ping 检查连接；等待 pong 最多 2 秒。无异步事件并不代表掉线。
   断线清空旧事件，重连重新发现端口和握手；没有 ACK 或事件重放。
3. type=wake、type=keyword 和 type=vad 各使用独立的 128 条有界队列，优先读取 wake，
   避免 keyword/vad 流量阻塞唤醒；断线清空三组队列，防止重放旧事件。
   超长输入行整行丢弃，不能把行尾 JSON 当作新事件。
4. Provider 将 keyword/score/doa 转为共享唤醒字段。节点原有流程生成
   interaction_id / wake_id，立即发布 EVT_VOICE_WAKEUP，再异步发布关联声纹结果。
   普通 keyword 仅接受“这儿／这里这里／到这来”，转为 EVT_VOICE_CALL（call_kind=here），
   不进入上述唤醒会话；其余普通关键词忽略。
5. `AudioSherpaProvider` 继续使用一条录音流，待机保留 3 秒历史音频；没有唤醒词
   时长时，声纹截取使用配置的 2.5 秒窗口。
6. 新配置 `interaction.wake_command_pre_roll_sec=0.3` 在收到 wake 的时间前保留
   300 ms 命令音频重叠，缓解紧接口令丢字。它不是精确的唤醒词结束时刻，可能包含
   唤醒词尾音，需要用标注录音验证并调节；默认旧配置仍为 0。

`audio_age_ms` 是候选音频帧到设备事件入队的时间，不是唤醒词时长或共享采样索引。
本实现不使用它推算精确命令分段边界。

## 角度与得分合约

AudioEvent 仍为 schema_version=2，增加字段见 `ROS2_CONTRACT.md`。
`wake_confidence=score`，不使用整型 confidence，也不除以讯飞的 1000。
score 是模型匹配分值，不是正确概率。保留原始分数及两种门槛用于诊断。

Flex 方向可用条件：doa_valid 为 JSON true，doa 在 [0,360)，年龄非负且有限，
`doa_age_ms + audio_age_ms + 主机排队时间 <= max_angle_age_ms`（默认 600 ms）。
主机排队时间计算到发布时刻，包含 provider 解码、节点轮询和发布前等待；
内部保存方向测量时刻，更新年龄时不会将已经计入的延迟重复相加。
`wake_angle_age_ms` 与 `header.stamp` 均以发布时间为准，发布前再次检查方向时效。
这里不包含不可测的设备发送/USB 延迟，不保证精确音频同步。
Provider 将无效方向标记为 `wake_angle=-1, wake_angle_valid=false`，保留年龄用于诊断。
节点仍发布 `EVT_VOICE_WAKEUP`，开始或刷新会话、启动命令录音，并按配置处理唤醒声纹。
方向无效记录 `wake_direction_unavailable result=invalid_angle`，唤醒接受仍记录
`wakeup_accepted`。下游收到后跳过声源转向/接近，继续等待语音命令。
有效 `0°` 正常接受。方向有效与否均遵守同一防抖窗口、整次事件年龄上限和注册期间拦截；
600 ms 仅限制方向，整次唤醒的新鲜度仍由 `wake_event_max_age_sec`（正式配置 8 s）限制。

本机下游源码已有以下保护，部署时须核对实际运行版本：

- `/home/cat/xbb/20260702_MarsDogTree`：无效或过期方向直接进入 WAITING，释放会话
  hold，不生成声源转向/接近候选；后续身份结果仍可关联。有效方向按原有流程处理。
- `/home/cat/xbb/20260707_MarsDogAction`：执行前再次检查方向有效性和 Flex 年龄，
  拒绝排队后过期的方向；attention 控制中的无效方向不能触发声源回退转向。

下游的 Flex 年龄上限为 600 ms；不要仅调大 Voice 的 max_angle_age_ms 就期待动作
使用更老方向。旧设备没有新增字段时沿用原有数值角度约定。

必须部署上述下游修改后再联动使用 Flex。旧消费端可能忽略有效性标志，把 -1 当作
真实角度。Voice 透传 microphone_array 原始角度，安装 offset/sign 仍只在 Action
应用一次；更换阵列后重新实测标定，不能直接认定旧 90°/-1 标定适用。

## VAD JSON 声源事件

正式 ReSpeaker 配置已启用 `sound_source`，共享现有 CDC reader。
直接判断 JSON：`type=vad,state=update,doa_valid=true` 时下发
`EVT_VOICE_SOUND_SOURCE_DETECTED`；false 和 start/end 不触发。每条有效更新一次，
不等待音频截取或分类。声源事件与唤醒、语音会话及主人身份独立。
DOA 时效、监听和下游通用序列接入见 [SOUND_SOURCE_EVENTS.md](SOUND_SOURCE_EVENTS.md)。

## 验收边界

- 离线测试覆盖分帧、非法字段、端口歧义、握手、心跳、重枚举、旧事件清理、
  无效/过期方向仍发布唤醒并启动录音、整次过期唤醒仍拒绝、零度有效唤醒、声纹窗口、命令音频重叠、
  真实设备失败不产生 Mock 事件。
- 实机录音收到 PCM 只证明音频通道可用。CDC pong 只证明协议连接。
- 真人测试分别记录两条唤醒词的命中、漏检和无口令误唤醒，观察 wakeup_accepted，
  核对同一 wake_id 的声纹结果；测试“旺财旺财，坐下”和会话内重复唤醒。
- 用固定方位检查角度、失效和过期分支；真实拔插恢复与离线模拟重连分别验收。
- 如果使用 AEC，播放需经过 Flex USB 扬声器端点，另验有声播放；本实现不更改
  播放设备或声纹阈值。麦克风改变后需复核原声纹样本的实际匹配效果。

定向测试命令：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q \
  tests/test_respeaker_wakeup.py tests/test_session_recovery.py \
  tests/test_audio_capture.py tests/test_voice_contract.py
```

关闭 pytest 插件自动加载用于隔离主机 ROS 测试插件；不影响节点运行。

## 2026-10-15 本次验证记录

- Flex 协议专项：28 项通过。Voice 定向回归：177 项通过、1 项排除。
- Voice 全量：619 项通过、20 项跳过、1 项失败。失败为原有词表数量断言
  `test_documented_qa_command_inventory_matches_current_code`：期望 64 条，当前
  `kws_keywords_raw.txt` 为 63 条；本次未修改该词表或该断言。全量之后新增的
  音频初始化失败不回退 Mock 用例已包含在定向回归中。
- Tree 定向回归：64 项通过；Action 定向回归：38 项通过。
- 离线串联 Flex Provider → AudioEvent → Tree 候选 → Action 上下文通过。
  Spin 使用 Python 假函数，没有连接机器人或调用 ROS 动作。
- 本机 sounddevice 5 秒检查收到 79360 个样本（4.96 秒）；arecord 检查收到
  77760 个样本（4.86 秒）。两者均为 16 kHz 单声道，不保存 PCM。
- CDC 释放后，新适配器收到真实 pong；与 UAC 并行 5 秒期间收到 79360 个音频样本，
  wake 事件为 0。因此连接与录音检查通过，真人唤醒、声纹、指令识别尚未验收。
- Voice 在 `/home/cat/marsdog_ws`、Tree/Action 在 `/home/cat/ros2_ws` 编译成功。
  已核对源码与安装内容及实际 import 路径；未启动或重启节点。

## 2026-10-16：位置呼唤词放入 ReSpeaker 普通 KWS

最多三个是 Flex 固件唤醒词表的容量；主机 Sherpa 没有此三词限制，
`config/wake_keywords.txt` 保留“旺财旺财／小火小火／小狗／跟屁虫”四个词。
“这儿／这里这里／到这来”使用 Flex 独立的普通 KWS 表（容量 16），
设备持续识别并上报 `type=keyword`，Voice 仅在已经处于唤醒会话时发布通知；方向无效时照常发布并标记 doa_valid=false；
不占用固件唤醒词名额，也不再由默认主机词表识别。

| ID | phrase（无声调拼音） | name |
|---:|---|---|
| 101 | `zhe er` | 这儿 |
| 102 | `zhe li zhe li` | 这里这里 |
| 103 | `dao zhe lai` | 到这来 |

固件通过 CDC 的 `add_keyword` 写入 NVS，重启保留，无需烧录；
修改前分页查询 `get_keywords`，避免覆盖其他词或占用 ID。设备为空时可逐条发送：

```json
{"cmd":"add_keyword","id":101,"phrase":"zhe er","name":"这儿"}
{"cmd":"add_keyword","id":102,"phrase":"zhe li zhe li","name":"这里这里"}
{"cmd":"add_keyword","id":103,"phrase":"dao zhe lai","name":"到这来"}
{"cmd":"get_keywords","offset":0,"limit":2}
```

查询按 `next_offset` 继续，直到 null；修改响应只显示第一页，不能据此判定三词均已写入。
`add_keyword` 已存在同 ID 时会报错，不应重复执行；`set_keywords` 是整表覆盖，应避免用于增量添加。
查询 `get_commands` 确认原有固件唤醒词没有变化。串口只能由一个程序使用，Voice 占用时先释放端口。

命中后仅在已有唤醒会话内发布 `EVT_VOICE_CALL`，call_kind=here，
intent_source 为 respeaker_keyword／对应识别来源。通知不创建或刷新会话，不取消当前录音或识别任务。
位置关键词与 ASR 词库／Model Intent 的昵称呼唤使用同一公开事件，保留来源和 here/name 子类。

串口线程先筛选方向，再将原始消息放入各自队列。VAD 仅接受 start/update，显式 active 必须为 true；
keyword 没有 state 要求。仅 doa_valid 严格为 true、角度有限且在 [0,360)、年龄有效的样本更新缓存。
VAD 方向计入 doa_age_ms，keyword 快照还须计入 audio_age_ms，避免把旧方向当作当前方向。
无效更新和 VAD end 不覆盖／刷新旧样本，乱序测量忽略；原始队列仍保留边界和无方向的关键词通知。
缓存独立于 sound_source.enabled，断线重连清空。按关键词接收前 2 秒窗口或本句音频区间
固定方向，ASR 排队前绑定，发布时重新计算总年龄（默认不得超过 600 ms）。
event_publish 日志同时展示呼唤的 doa、doa_valid、doa_age_ms、doa_source。
没有合格方向或方向过期时仍发布呼唤，doa=-1、doa_valid=false，下游跳过转向。
方向、年龄、来源使用 doa/doa_valid/doa_age_ms/doa_source；wake_* 只保留关键词诊断，
wake_angle_valid=false。interaction_id 关联当前会话，wake_id 为空，两个执行标志仍为 false。

同次固件／ASR 命中按声学时间去重；未唤醒或整条过期的命中不占用去重状态。
完整迁移和下游语义见 [ROS2 契约](ROS2_CONTRACT.md#统一呼唤2026-10-19)。
2026-10-19 的统一契约取代下方历史记录中针对 CALL_HERE 的方向恢复规则。

设备词表写入后立即生效；主机接收路由需重新启动 Voice 节点。
真人命中率及短词误报率须现场验证。

## 2026-10-15 晚：主机 sherpa 同时听唤醒词（下游执行侧代改）

固件唤醒漏得多：10-15 上午录音里喊 15 声「旺财旺财」，固件只认出 7 声；同一段录音用仓库里的
sherpa KWS 模型能认出 12 声。语音负责人这边没空，下游执行侧（lite3_demo_ws）代改如下，没有提交：

- 新唤醒类型 `providers.wakeup.type: respeaker_sherpa`（`providers/wakeup_respeaker_sherpa.py`）：
  继承 `respeaker_serial`，固件唤醒照常；另起一个 sherpa 关键词识别线程，在常开麦克风（待机音频，
  `audio.wake_history_sec > 0`）上听 `config/wake_keywords.txt` 里的唤醒词。两路谁先认出算谁，
  `dedupe_sec`（1.5 秒）内的第二个当同一声丢掉。位置呼唤通知由节点通过会话/方向条件后单独防抖。
- sherpa 唤醒的方向取 `doa_window_sec`（2 秒）内 Flex 串口任意消息里的方向，
  当前版本先筛选总年龄不超过 `max_angle_age_ms` 的候选，再优先取最新 `doa_source=auto`；
  没有就按无效方向发（`wake_angle=-1`、`wake_angle_valid=false`）。解码等待也计入方向年龄。
  `received_monotonic` 是认出时那块音频的采集时间，声纹快照和指令录音都以它为准。
- 为此改的原有文件：`respeaker_serial_reader.py` 记下各消息里的有效方向（`recent_doa`）；
  `audio_capture.py` 的 `BufferedAudioCapture` 加 `listener`，每块音频（含待机）都交给它；
  `audio_sherpa.py` 加 `set_standby_listener`；`voice_interaction_node.py` 认新类型，并在建好
  音频后调用唤醒 provider 的 `attach_audio`；`config_loader.py` 解析两个新路径。
  install 里给新模块和 `wake_keywords.txt` 补了软链（同 symlink-install）。
- 退回固件唤醒：把 `type` 改回 `respeaker_serial`，其他配置项不用删。
- 验证：上午录音实跑，固件 7 声、sherpa 12 声（方向和固件一致）、合起来去重 13 声；
  `log/voice_debug` 里 200 段口令录音（17 分钟）sherpa 误认 0 次；60 秒音频单线程 5 秒算完。
  全量测试 794 通过、20 跳过（新增 `tests/test_respeaker_sherpa_wakeup.py` 4 项）。
  节点实起 25 秒：串口、sherpa、麦克风都起来，期间屋里有人说话 sherpa 没误唤醒。真人喊唤醒词还没测。

## 2026-10-15 晚：走快点 / 走慢点补口语说法（下游执行侧代改）

真狗跟随时说「快点」「快一点」「走快一点」「快走」「慢一点」都被当成闲聊：词库只有表上 6 句
（走快点、快点走、快跟上、走慢点、慢点走、慢慢走），又是整句精确匹配。只在
`config/command_catalog_rules.yaml` 的 `phrase_variants` 里给 SPEED_UP、SLOW_DOWN 加了口语说法，
再跑 `scripts/generate_command_catalog.py` 重新生成 `command_catalog.yaml`、
`docs/COMMAND_CATALOG_TEST_MATRIX.md`、`docs/DOWNSTREAM_COMMAND_MAP.csv`（`kws_keywords_raw.txt` 不变，
版本号没动）。没有撞上别的口令；仍是整句匹配，「快点过来」「慢走」不受影响。

- SPEED_UP：快点、快一点、快些、快一些、走快一点、走快些、走快一些、再快点、再快一点、快点快点、快走、跑快点、跑快一点
- SLOW_DOWN：慢点、慢一点、慢些、慢一些、走慢一点、走慢些、走慢一些、再慢点、再慢一点、慢点慢点、跑慢点
- 验证：全量测试改前改后同样 218 失败、576 通过、20 跳过，失败的用例一模一样（测试桩缺
  `_utterance_speaker_event_fields`，是节点新改动没跟上，与词库无关）。

## 2026-10-15 晚：固件唤醒方向过期时取说唤醒词那段的方向（下游执行侧代改）

19:51–20:27 实测 46 次唤醒（唤醒词已是「小火小火」），16 次方向无效：固件给的 doa 在认出唤醒词时
已过去 780–1570 ms，超过 `max_angle_age_ms`（600 ms）被作废，下游没方向只能原地转圈找人。
四个字的唤醒词说完要一秒多，固件的方向多半是在词头测的。

- 只改 `providers/wakeup_respeaker_sherpa.py`：固件唤醒方向无效时，和 sherpa 唤醒一样取
  `doa_window_sec`（2 秒）内 Flex 串口消息里最新的有效 `doa`（优先 `doa_source=auto`），补成有效方向，
  `wake_angle_age_ms` 按补上的那条算；这段时间里没有有效方向就照旧发无效方向。日志 `Flex wakeup bearing taken from the word`。
- `respeaker_serial` 类型不受影响；没动 `max_angle_age_ms` 和 `respeaker_serial_reader.py`。
- 2026-10-19 更新：上述恢复现在同样受 600 ms 总年龄限制，2 秒仅为历史检索窗口。
  已修复 reader 缓存识别快照漏算 `audio_age_ms`，并支持 CALL_HERE 的过期快照恢复；
  发布前重新计算年龄和检查有效性，方向年龄与发布时间对齐。
- 验证：`tests/test_respeaker_sherpa_wakeup.py` 加 2 项（过期方向补上、窗口内没有方向保持无效）；
  全量测试改前 218 失败 602 通过、改后 218 失败 604 通过，失败的用例一模一样（与本改动无关）。
  要重启语音节点才生效。

## 2026-10-15 晚：主机 sherpa 唤醒词加「小火小火」（下游执行侧代改）

固件唤醒词已是「小火小火」，主机 sherpa 却只听「旺财旺财」，小火小火只靠固件，漏听多。
`config/wake_keywords.txt` 加一行 `x iǎo h uǒ x iǎo h uǒ @小火小火`，两个词都听，阈值不变（0.25），代码没动。

- 四个音素都在模型 `tokens.txt` 里。
- 误认：10-15 的 `log/voice_debug` 200 段（16.5 分钟，唤醒后的口令和人声）、上午的旺财录音 1 分钟、
  声纹录入 3 段、模型自带 `test_wavs` 9 段，两个词都没认出过。
- 旺财录音 15 声仍认出 12 声，和只听旺财时一样。
- 还没有「小火小火」的录音，认出率没测；现场看日志 `Sherpa wakeup: word='小火小火'` 和 `Flex wakeup` 各认出几声。
- 要重启语音节点才生效。

## 2026-10-15 晚：新需求表补低头、抬头、这是啥、我是谁，倒下来归装死（下游执行侧代改）

10-15 晚的新需求表（第 31/32/83/84 行）要低头、抬头、「这是啥」（找东西）、「我是谁」（找人），工作簿
`docs/语音识别内容.xlsx` 里没有这几条；「倒下来」和装死放一起。工作簿没动，改的是生成规则：

- `config/command_catalog_rules.yaml`：`phrase_variants.PLAY_DEAD` 加「倒下来」；`expansion.command_profiles`
  加 WHAT_IS_THIS、WHO_IS_IT（query）；新加顶层 `extra_commands`，放工作簿外的口令，都是 `core: false`、
  `control: DO`，事件 `EVT_VOICE_COMMAND_<KEY>`。
- `scripts/generate_command_catalog.py`：`build()` 把 `extra_commands` 接在工作簿口令后面（`source_rows` 为空）；
  测试矩阵里这几条行号写「新表」，表头条数按实际算。
- 重新生成 `command_catalog.yaml`、`docs/COMMAND_CATALOG_TEST_MATRIX.md`、`docs/DOWNSTREAM_COMMAND_MAP.csv`，
  `--check` 通过；`kws_keywords_raw.txt` 不变，版本号没动。

- LOOK_DOWN：低头、看地板、头低一点
- LOOK_UP：抬头、往上看、看上边
- WHAT_IS_THIS：你看看这是啥、快看、看过来、瞧瞧这是啥、你瞅瞅这是啥、这是什么、这是啥、那是啥、那是什么、这是什么东西、看前边
- WHO_IS_IT：看、看看谁回来了、看下谁来了、这是谁、我是谁、你知道我是谁吗、他是谁、谁呀、什么人
- 「说话」仍是 BARK（工作簿第 18 行），没改。
- 口令 38 → 42 条（核心 21 不变），整句 194 → 220，扩展 1940 → 2200。
- 测试：`tests/test_command_lexicon.py` 按新条数改数字；「这是啥」「这是什么」新表又要了，从
  `test_removed_catalog_routes_do_not_survive_as_aliases` 里拿掉。`tests/test_workbook_catalog.py` 条数 43 → 47，
  加 `test_new_table_phrases_outside_workbook_are_executable`（13 句）。
- 验证：全量测试改前 184 失败 644 通过 20 跳过，改后 184 失败 655 通过 20 跳过，失败的用例一模一样（与本改动无关）。
- 「我是谁」「这是啥」下游按 `EVT_VOICE_SOUND_SOURCE_DETECTED` 的方向找人，语音这边不用再改。要重启语音节点才生效。
