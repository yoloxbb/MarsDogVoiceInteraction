# ReSpeaker Flex 麦克风使用与修改原理

> 适用项目：`flex_xiao`  
> 硬件：ReSpeaker Flex + 圆形四麦小板（FPC 连接、360°）+ XIAO ESP32S3  
> 应用固件：**0.5.0**，USB UAC2 麦克风/扬声器 + CDC ACM  
> 识别组件：ESP-SR **2.4.6 / MultiNet7 中文模型 `mn7_cn`**  
> 文档更新：**2026-10-14**

## 1. 文档用途

本文说明当前固件的 USB 音频、VAD、唤醒词、普通关键字、声源角度、软件增益和回声消除。JSON 事件示例用于解释字段；实测结果单独列在第 8.6 节。

需要先明确三点：

1. 麦克风音频通过 **USB UAC** 传输，CDC 串口不传输 PCM 音频。
2. `vad` 是通用声音活动事件，可用于主机截取声音片段；它不等同于人声检测，也不依赖命令词唤醒。
3. `wake` 是唤醒词事件，`keyword` 是普通关键字事件；两类持续识别，普通关键字不需要先唤醒。二者共用 MultiNet，按词表区分事件；本固件没有单独训练的 WakeNet 唤醒模型。

截至本次更新，设备已读回核对的配置：

| 项目 | 当前值 |
|---|---|
| 唤醒词 ID 1 | **旺财旺财**，`wang cai wang cai` |
| 唤醒词 ID 2 | **小火小火**，`xiao huo xiao huo` |
| 普通关键字 | 空；临时测试词“打开灯光”“关闭灯光”已清除 |
| VAD 事件灵敏度 | `medium`（默认） |
| 最终事件门槛 `min_score` | `0.02`（默认） |
| 识别 / USB 麦克风通道 | `input_slot=0` / `uac_slot=0`（默认） |

两条新唤醒词已保存到 NVS，立即生效、重启保留；尚未完成这两条词的真人准确率测试。旧的“孙悟空”“呆子”“二师兄”已从设备唤醒词表移除。

**设备词表与编译默认表不同。** 当前 `tools/commands_zh.json` 和固件内置表仍为“八戒／呆子／二师兄”；`reset_commands` 会恢复内置表，不能用它恢复“旺财旺财／小火小火”。修改运行时词表无需烧录，恢复当前两条词的命令见第 6.3 节。

## 2. 快速开始

### 2.1 确认设备处于应用模式

使用支持数据传输的 USB 线连接电脑。正常应用模式下，一块板会枚举成同一个 USB 复合设备的多个逻辑接口：

- USB 麦克风；
- USB 扬声器；
- CDC ACM 虚拟串口。

当前设备信息：

| 项目 | 值 |
|---|---|
| 应用模式 VID:PID | `303A:4013` |
| 下载模式 VID:PID | `303A:1001` |
| 产品名 | `ReSpeaker Flex UAC I/O + CDC` |
| 序列号 | `FLEX-ESP32S3-0001` |
| CDC 接口名 | `ReSpeaker Flex Metadata` |

进入下载模式后，应用固件停止运行，麦克风、扬声器和应用 CDC 都会暂时消失，这是正常现象。烧录完成后松开 BOOT，短按 **XIAO 小板上的 RESET**，设备应重新以 `303A:4013` 枚举。Flex 主板的 SafeMode 不是 XIAO 下载按键。

Windows 可能在每次重枚举后分配不同的 COM 号，因此主机程序最好根据 VID、PID、产品名或序列号查找端口，不要永久写死 COM 号。

### 2.2 测试 USB 麦克风

1. 打开“设置 → 系统 → 声音”。
2. 在输入设备中选择 ReSpeaker Flex 对应的麦克风。
3. 打开 Windows 录音机并录制一段近距离讲话。
4. 初次测试时关闭 Windows 的自动增益、降噪和“音频增强”，避免系统后处理干扰判断。
5. 建议先在距离阵列约 `0.3～1 m` 处测试。

当前 UAC 麦克风格式固定为：

| 参数 | 值 |
|---|---|
| 采样率 | `16000 Hz` |
| 位深 | `16 bit` 有符号 PCM |
| 声道 | 单声道 |

录音音量比消费级 USB 麦克风稍小不一定是故障。固件保留了削波余量，并且不会始终固定增加增益。判断是否异常时应结合波形、`raw_rms`、`rms` 和 `speech`，不能只凭电脑扬声器的回放音量。

### 2.3 测试 USB 扬声器

1. 将与板卡及供电匹配的扬声器接到 Flex 扬声器接口，不要直接连接 ESP32 GPIO。
2. 核对扬声器阻抗和额定功率。Seeed 当前官方资料标明功放支持驱动 `10 W / 4 Ω` 扬声器；使用较小功率单元时仍需限制输出音量。这不是当前已连接扬声器的型号声明。[官方硬件说明](https://wiki.seeedstudio.com/respeaker_flex_introduction/)
3. 在 Windows 输出设备中选择 ReSpeaker Flex 对应的扬声器/耳机设备。
4. 首次播放从 10% 系统音量开始，确认无破音后逐步提高。
5. 需要较大输出功率时，应按板卡要求提供稳定的外部供电；仅靠 USB 供电时最大音量可能受限。

电脑发送给设备的 UAC 播放格式为 `16 kHz / 16 bit / 双声道`。固件将左右声道平均为单声道，再复制到 I2S 输出时隙，以便扬声器播放并给 XVF3800 提供一致的 AEC 远端参考。

### 2.4 测试 CDC 串口

安装依赖：

```powershell
python -m pip install pyserial
```

列出端口：

```powershell
pio device list
```

将示例中的 `COM15` 替换成当前应用 CDC 端口：

```powershell
python tools\serial_monitor.py COM15 --ping
python tools\serial_monitor.py COM15 --status
python tools\serial_monitor.py COM15 --list-commands
python tools\serial_monitor.py COM15
```

最后一条命令进入持续监视模式。设备没有事件时保持安静，不会每秒发送状态。USB CDC 的实际传输不依赖传统串口波特率，`115200` 只是串口库使用的兼容参数。

## 3. 麦克风音频链路

当前录音链路如下：

```text
圆形四麦小板（通过 FPC 连接主板）
  → XVF3800 阵列处理 / 波束形成 / AEC
  → I2S 32 bit 双时隙
  → ESP32-S3 分别转换通道 0 / 1 为 16 bit PCM
  → 每通道独立计算 RMS、人声 VAD、声音活动与增益
  ├─ uac_slot（默认 0）→ USB UAC 麦克风 → 电脑录音/主机算法
  └─ input_slot（默认 0）→ 完整帧队列 → MultiNet
                                      ├─ 唤醒词 → CDC wake
                                      └─ 普通关键字 → CDC keyword

识别通道的声音活动 → CDC vad/start、end、update
XVF3800 I2C 方向 → 有效性检查、方向跟踪 → 三类事件的角度字段
```

### 3.1 当前默认选择通道 0

0.4.2 起，两路 I2S 输入独立维护 VAD、底噪与增益。默认 `AUDIO_I2S_VOICE_SLOT=0`，USB 麦克风和识别器均取通道 0。

**实际来源由 XMOS 输出路由决定**，不能只凭左右声道编号认定哪一路是唤醒或 ASR。`get_doa` 返回 `output_left`、`output_right`、`packed`；`get_audio_diagnostics` 返回两路 RMS。

运行时可分别选择，两项只能为整数 0 或 1：

```json
{"cmd":"set_recognition","input_slot":0,"uac_slot":0}
```

省略字段保持原值，设置不写 NVS、重启恢复默认 0。切换识别通道会重置解码上下文。若两项不同，`get_status` 的人声、活动、RMS 与底噪描述识别通道，不能直接解释 USB 录音通道。

### 3.2 缓冲和时钟漂移

USB 与 I2S 使用不同的时钟源，长期运行时会产生微小漂移。当前实现使用：

- 16 KB 麦克风环形缓冲区；
- 约 60 ms 预填充；
- USB 每 1 ms 发送 16 个采样点；
- 根据缓冲水位读取 15、16 或 17 个点，再线性插值成 16 个点；欠载后重新预填充。

识别链路按完整 20 ms 帧排队，再连续拼接成模型输入块。断流或通道改变会重置解码器；队列中超过 200 ms 的过期帧丢弃并计数，避免识别陈旧音频。

如果输入设备周期性出现“噗呲”、爆音或断续，应查询 `uac_underrun` 和 `uac_overflow`。两个计数持续增长通常表示缓冲欠载/溢出、USB 抖动、I2S 时钟异常或任务调度不及时。

### 3.3 条件式软件增益

固件不是固定增加 `+8 dB`。只有满足以下条件时，增益才会平滑上升：

- ESP-SR 判断当前是有效人声，即 `speech=1`；
- 软件增益前的 RMS 低于目标值。

增益算法现位于 `src/audio_processing.h/.c`，旧版 Q12 增益宏已不适用：

| 宏 | 当前值 | 含义 |
|---|---:|---|
| `AUDIO_GAIN_MAX` | `2.5` | 最大约 +7.96 dB |
| `AUDIO_GAIN_TARGET_RMS` | `0.063` | 较小人声的目标 RMS |
| `AUDIO_GAIN_HEADROOM_PEAK` | `29204` | 保留约 1 dB 峰值余量 |

人声判断有效且原始电平明显高于底噪时，才允许向目标增益提升；其余情况逐渐回落。算法按帧检查峰值，并在采样点间平滑增益；突发大声时可将增益压到 1 以下。输入到 ESP32 前已经削波的失真无法靠软件恢复，也不能仅凭增益状态认定回声已消除。

`raw_rms` 是 ESP32 软件增益前的电平，但仍可能已经过 XMOS 处理；`rms` 是 ESP32 增益后的电平。

## 4. CDC 通信基础

CDC 使用 UTF-8 JSON Lines：每条消息都是一个 JSON 对象，末尾必须有换行符 `\n`。

单条请求最多 1023 字节，不含换行；中文名称按 UTF-8 编码。超长行整行丢弃并报错，不会执行行尾片段。

主机打开端口后建议：

1. 设置 `DTR=True`、`RTS=False`；
2. 等待 `300～1000 ms`；
3. 发送 `{"cmd":"ping"}`；
4. 收到 `{"type":"pong"}` 后再执行其他命令。

CDC 是事件通道，不是可靠消息队列。设备断开、主机没有打开端口或发送缓冲区不可用时，异步事件可能丢失；设备不会为旧事件重发，也没有 ACK。主机在 USB 重连后应重新发现端口并恢复状态。

完整字段、错误码和主机示例见项目根目录的 `SERIAL_PROTOCOL.md`。

当前不会在打开 CDC 时自动发送 `hello`；发送 `get_info` 查询固件版本。其他常用查询：

```json
{"cmd":"get_info"}
{"cmd":"get_keywords","offset":0,"limit":2}
{"cmd":"get_vad"}
{"cmd":"get_audio_diagnostics"}
{"cmd":"get_runtime"}
{"cmd":"get_doa"}
```

`get_runtime` 返回运行时间、复位原因、最小可用内部堆和各任务历史最小栈余量。诊断计数应比较测试前后增量；`emitted` 和旧名 `wake_send_errors` 在 0.5.0 中同时计入 wake 与 keyword。

### 4.1 查询状态

发送：

```json
{"cmd":"get_status"}
```

典型响应：

```json
{"type":"status","doa":95,"doa_valid":true,"doa_age_ms":30,"doa_source":"auto","activity":1,"speech":0,"xmos_speech":0,"raw_rms":0.0062,"rms":0.0062,"noise_floor":0.0006,"uac_underrun":0,"uac_overflow":0,"uac_ctrl_calls":8,"uac_ctrl_mask":46,"uac_ctrl_bad":0,"aec_valid":true,"aec_bypass":0,"aec_farends":1,"aec_converged":1,"i2s_inactive":0,"aec_ref":1,"aec_delay":0,"echo_suppress":1,"nla":1}
```

常用字段：

| 字段 | 含义 |
|---|---|
| `doa` / `doa_valid` | 方向 0～359°；无效时为 -1 / false |
| `doa_age_ms` / `doa_source` | 方向样本年龄与来源；未取得样本时年龄为 -1 |
| `activity` | 通用声音活动状态 |
| `speech` | ESP-SR 人声 VAD 状态 |
| `xmos_speech` | XVF3800 原始语音状态，仅用于诊断 |
| `raw_rms` | ESP32 软件增益前 RMS |
| `rms` | 软件增益后 RMS |
| `noise_floor` | 通用活动检测器估计的环境噪声底 |
| `uac_underrun` | UAC 缓冲欠载累计计数 |
| `uac_overflow` | UAC 缓冲溢出累计计数 |
| `aec_ref` | AEC 参考链路是否存在 |
| `aec_bypass` | AEC 是否旁路，正常应为 0 |
| `aec_converged` | AEC 自适应滤波器是否已收敛 |
| `aec_delay` | 当前 AEC 系统延迟，单位为采样点 |
| `nla` | 非线性残余回声抑制是否启用 |

### 4.2 主机程序读取原则

响应和异步事件可能交错到达。例如主机等待 `status` 时，也可能先收到 `vad`。协议没有请求 ID，建议一次只保留一个未完成请求，并按 `type` 分发，不能假定下一行就是刚才请求的响应。

最小读取框架：

```python
import json
import time
import serial

with serial.Serial("COM15", 115200, timeout=0.2) as port:
    port.dtr = True
    port.rts = False
    time.sleep(0.3)
    pending = bytearray()
    while True:
        pending.extend(port.read(port.in_waiting or 1))
        while b"\n" in pending:
            raw, _, rest = pending.partition(b"\n")
            pending = bytearray(rest)
            try:
                event = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind in ("wake", "keyword"):
                angle = event["doa"] if event.get("doa_valid") else None
                print(kind, event["id"], event["keyword"], event["score"], angle)
            elif kind == "vad":
                # update 只更新当前片段方向，不是新片段开始。
                print("VAD", event["state"], event["doa"], event["doa_valid"])
            else:
                print(kind, event)
        if len(pending) > 4096:
            raise RuntimeError("未收到完整换行，请检查端口和数据格式")
```

两类识别词的 ID 可以相同，主机用 `(type,id)` 区分。不要要求先收到 `vad/start` 才接受识别结果：识别门控与 VAD 事件灵敏度分开，`wake` / `keyword` 中的 `activity=0` 也是允许的。

## 5. VAD 与主机 YAMNet 截取

### 5.1 声音活动与方向事件

`vad` 表示通用声音活动，可能由人声、音乐、敲击、犬吠等触发；它不要求先唤醒，也不要求 `speech=1`。为降低小噪声误触发，默认档会忽略未达到持续时间的短促声音，不能保证每次短敲击都触发。

声音开始时可能尚未取得角度：

```json
{"type":"vad","source":"audio_activity","state":"start","active":true,"doa":-1,"doa_valid":false,"doa_age_ms":-1,"doa_source":"none","raw_rms":0.012,"noise_floor":0.0006,"speech":0,"xmos_speech":0,"rms":0.012}
```

活动期间取得有效方向后补发更新：

```json
{"type":"vad","source":"audio_activity","state":"update","active":true,"doa":95,"doa_valid":true,"doa_age_ms":30,"doa_source":"auto","raw_rms":0.018,"noise_floor":0.0006,"speech":1,"xmos_speech":1,"rms":0.025}
```

声音结束时旧角度可能已过期：

```json
{"type":"vad","source":"audio_activity","state":"end","active":false,"doa":-1,"doa_valid":false,"doa_age_ms":670,"doa_source":"none","raw_rms":0.0007,"noise_floor":0.0006,"speech":0,"xmos_speech":0,"rms":0.0007}
```

`start` / `end` 表示片段边界；`update` 仅更新当前片段方向。方向由无效变有效、变为无效或与上次上报相差至少 15° 时可触发 `update`，最多每 200 ms 一次。非人声也可能触发 VAD，但 XMOS 不一定提供有效方向。

### 5.2 当前三档灵敏度

参数位于 `src/audio_processing.c` 的 `s_profiles`，音频帧为 20 ms：

| 档位 | 开始 RMS 下限 | 结束 RMS 下限 | 开始 / 结束底噪倍率 | 开始确认 | 安静结束 | 单帧强声触发 |
|---|---:|---:|---|---:|---:|---|
| `low` | 0.008 | 0.004 | 5.0 / 2.5 | 120 ms | 500 ms | 关闭 |
| **`medium` 默认** | **0.006** | **0.003** | **4.0 / 2.0** | **80 ms** | **600 ms** | **关闭** |
| `high` 旧版灵敏度 | 0.002 | 0.0012 | 3.0 / 1.8 | 60 ms | 800 ms | 达开始门限 3 倍可直接触发 |

动态开始门限 = `max(开始 RMS 下限, noise_floor × 开始倍率)`，结束门限同理。固件边检测边估计底噪，不屏蔽上电后的第一秒。持续约 2 秒的非语音背景会逐步纳入底噪，避免稳定风扇声让活动长期不结束。

这些档位只控制对外 `vad` 事件。MultiNet 使用独立的识别活动门控；ESP-SR 人声 VAD 参数在 `board_config.h`，当前人声开始/结束保持为 160 / 700 ms，不要与本表混淆。

### 5.3 不烧录即可调节

```json
{"cmd":"get_vad"}
{"cmd":"set_vad","sensitivity":"low"}
{"cmd":"set_vad","sensitivity":"medium"}
```

成功返回 `type=vad_config`，包含实际门限、`start_ms`、`end_ms`、`impulse_trigger` 及 `persisted=false`。**VAD 设置只在本次启动有效，重启恢复 medium。** 若主机需要一直使用 low，应在设备每次启动后重新设置。

VAD 过敏时先比较 medium 与 low；只有需要捕获更短、更弱事件时才考虑 high。提高门槛可能漏掉远处或短促声音，不能只看误触发减少。

在 **MarsDogVoiceInteraction 项目根目录**，可以使用主机脚本自动发现 Flex CDC、
握手并查询或设置档位：

```bash
# 查询当前配置；不带参数也只查询
.venv/bin/python scripts/set_respeaker_vad.py --get

# 降低灵敏度，并重新查询确认实际生效档位
.venv/bin/python scripts/set_respeaker_vad.py --sensitivity low

# 恢复默认档位，或提高灵敏度：按需要选择一条执行
.venv/bin/python scripts/set_respeaker_vad.py --sensitivity medium
.venv/bin/python scripts/set_respeaker_vad.py --sensitivity high

# 手动指定端口，或通过 USB 序列号选择多块设备中的一块
.venv/bin/python scripts/set_respeaker_vad.py --port /dev/ttyACM0 --sensitivity low
.venv/bin/python scripts/set_respeaker_vad.py --serial-number FLEX-ESP32S3-0001 --get
```

脚本使用项目已有的 `pyserial` 依赖。先释放使用同一 CDC 串口的 Voice 节点或监视器；
Linux 检出占用时脚本直接退出，不会停止其他进程。脚本忽略交错的异步事件，
设置后发送 `get_vad` 核对 `sensitivity`，并打印固件实际返回的门限。
这是一次性设置，设备重启后需重新运行；只支持固件的三档配置，不能直接指定任意 RMS 数值。

### 5.4 YAMNet 截取方式

CDC 事件与 UAC PCM 使用不同 USB 接口，没有精确的共享采样序号。主机应持续录音并保留最近 300～500 ms 的环形预录音：

1. 收到 `vad/start`，将预录音拼到新片段开头，继续收集 PCM。
2. 收到 `vad/update`，只更新片段的方向记录，不创建新片段。
3. 收到 `vad/end`，按业务需要保留少量尾音后结束片段。
4. 转换为主机侧 YAMNet 所需格式后分类。

不要因为 `speech=0` 丢弃非人声片段；也不要把 `doa=-1` 当成真实方向。若保留片段内最后一次有效角度，必须同时记录其时间，避免把历史角度显示为当前角度。

### 5.5 MarsDog 主机声源事件

MarsDog Voice 已新增基于 CDC JSON 的 `EVT_VOICE_SOUND_SOURCE_DETECTED`，发布到
`/perception/audio_event`。直接根据 CDC JSON 判断：
`type=vad,state=update,doa_valid=true` 且角度、年龄有效时下发事件，false 不触发。
每条符合条件的 update 发布一次，不要求 start、不等待 end 或主机 YAMNet 分类。

`speech=0` 的声音活动也能触发，事件携带原始 DOA，以及
`sound_trigger_stamp/doa_age_ms` 供下游检查时效。JSON 本身不包含犬吠、拍手等具体声音类别。
声源识别独立于唤醒和主人身份识别；“转向、靠近、人脸/物体/姿态识别”通用序列
由下游 Tree/Action 接入这个新事件实现。
完整主机配置、字段及监听命令见 [SOUND_SOURCE_EVENTS.md](SOUND_SOURCE_EVENTS.md)。

## 6. 唤醒词、普通关键字与模型得分

### 6.1 两类词表和当前模型

当前启用 **MultiNet7 中文模型 `mn7_cn`**，不是中英双语模型。项目可构建另一套英文版本，但当前固件没有同时启用英文，也没有通过协议在线切换语言的接口。

| 类别 | 容量 | 是否持续识别 | 上报事件 | 保存位置 |
|---|---:|---|---|---|
| 唤醒词 | 1～3 条 | 是 | `wake` | NVS `flex_cmd/commands` |
| 普通关键字 | 0～16 条 | 是，不需要先唤醒 | `keyword` | NVS `flex_cmd/keywords` |

两组共用识别器，分别管理配置和事件。**协议加词是在更新识别词表，不是在训练模型。** `name` 只决定显示名称，发音由 `phrase` 定义。

以下事件数值仅用于解释字段，不是新词实测成绩：

```json
{"type":"wake","engine":"multinet","keyword":"旺财旺财","id":1,"confidence":8,"score":0.08,"threshold":0.02,"min_score":0.02,"audio_age_ms":180,"doa":95,"doa_valid":true,"doa_age_ms":80,"doa_source":"auto","activity":1,"speech":1,"xmos_speech":1,"rms":0.0312}
{"type":"keyword","engine":"multinet","keyword":"打开灯光","id":101,"confidence":7,"score":0.07,"threshold":0.02,"min_score":0.02,"audio_age_ms":180,"doa":95,"doa_valid":true,"doa_age_ms":80,"doa_source":"auto","activity":0,"speech":1,"xmos_speech":0,"rms":0.002}
```

### 6.2 得分不是识别正确率

`score` 是模型对候选词的内部匹配评分，范围 0～1，可用于门槛筛选。它**没有校准为“这次识别正确的概率”**，也不是在本设备上测出来的准确率。

兼容字段只做数值转换：

```text
confidence = round(score × 100)
0.052 → confidence=5；不是“有 5.2% 概率正确”。
```

0.5.0 在首次候选后继续分析约 160 ms（模型超时可提前结束），保留窗口内最高原始得分，再检查最终门槛、对应音频的人声/活动状态和同词冷却。

| 字段 | 含义 | 当前默认值 |
|---|---|---:|
| `threshold` | 模型产生候选的门槛 | 0.02 |
| `min_score` | 固件最终上报的最低得分 | 0.02 |
| `confirm_ms` | 候选确认窗口，通过诊断返回 | 160 ms |
| `cooldown_ms` | 同一词的重复事件冷却 | 1000 ms |

旧词表实测中，用户没有说“二师兄”，却出现得分约 0.052 的该词事件；“孙悟空”事件的得分约为 0.038。错误词也可能得到更高分。因此只提高门槛不能保证同时解决误报和漏报，`score/threshold` 的比值也不是校准概率。

真正评估效果，要记录实际说了什么、识别结果、漏检次数，以及未说口令时的误触发次数。已有日志不能给出新唤醒词“旺财旺财／小火小火”的识别准确率。

### 6.3 修改唤醒词

当前设备两条唤醒词：

| ID | `phrase` | `name` |
|---:|---|---|
| 1 | `wang cai wang cai` | 旺财旺财 |
| 2 | `xiao huo xiao huo` | 小火小火 |

查询与恢复这两条词（整表替换会删除未列出的旧唤醒词）：

```json
{"cmd":"get_commands"}
{"cmd":"set_commands","commands":[{"id":1,"phrase":"wang cai wang cai","name":"旺财旺财"},{"id":2,"phrase":"xiao huo xiao huo","name":"小火小火"}]}
```

成功响应为 `type=commands,status=applied,custom=true`，并返回完整词表；修改立即应用并写入 NVS，重启保留。

监视器也支持：

```powershell
python tools\serial_monitor.py COM15 --list-commands
python tools\serial_monitor.py COM15 --set-commands `
  --command 1 "wang cai wang cai" "旺财旺财" `
  --command 2 "xiao huo xiao huo" "小火小火"
```

`add_command` / `update_command` 当前都按 ID 插入或更新；超过 3 条会失败。`delete_command` 不能删除最后一条。`reset_commands` 删除 NVS 覆盖并恢复编译默认表，当前为“八戒／呆子／二师兄”；这与查询或重启不是同一个操作。

### 6.4 普通关键字独立增删改查

以下依次演示新增、查询、修改和删除。发送修改类命令就会改变设备配置：

```json
{"cmd":"add_keyword","id":101,"phrase":"da kai deng guang","name":"打开灯光"}
{"cmd":"get_keywords","offset":0,"limit":2}
{"cmd":"update_keyword","id":101,"phrase":"da kai dian deng","name":"开灯"}
{"cmd":"delete_keyword","id":101}
```

批量替换或清空：

```json
{"cmd":"set_keywords","keywords":[{"id":101,"phrase":"da kai deng guang","name":"打开灯光"},{"id":102,"phrase":"guan bi deng guang","name":"关闭灯光"}]}
{"cmd":"clear_keywords"}
```

规则：

- `id` 为 1～32767 的整数，在各自词表内唯一；唤醒词和关键字可使用相同 ID。
- `phrase` / `name` 各最多 63 个 UTF-8 字节。中文 `phrase` 使用无声调拼音，音节之间空格分开；`name` 可为中文。
- 固件规范化大小写与空白；规范化后的同一词组不能在两类词表中重复。
- 普通关键字的 `add_keyword` 严格新增，ID 已存在报错；`update_keyword` 要求 ID 已存在。这与旧唤醒词的插入或更新行为不同。
- `set_keywords` 整表替换，允许 `[]`；模型校验或保存失败会尝试回退，不应把错误响应当作配置成功。
- 修改保存到独立 NVS 键，重启保留，不改变唤醒词表。
- 固件可接受单音节拼音，但短词更需测试误触发；旧构建脚本 `flash_variant.py` 的中文默认词表校验仍要求至少两个音节。两者校验入口不同。

查询或修改返回 `type=keywords`。查询 `offset` 默认 0，`limit` 为 1～2、默认 2；`next_offset=null` 表示结束，否则用返回的偏移继续查询。JSON 转义较长时一页可能只有 1 条。修改响应也只包含第一页，不能当作完整词表。

```json
{"type":"keywords","status":"ok","language":"zh","total":1,"capacity":16,"offset":0,"keywords":[{"id":101,"phrase":"da kai deng guang","name":"打开灯光"}],"next_offset":null}
```

请求仍受 1023 字节限制，较长词表应逐条 `add_keyword`。`list_keywords` 是 `get_keywords` 的别名。两类词共用识别门槛，按内部词条分别去重，相同外部 ID 不会共用冷却记录。

### 6.5 中文、英文与编译默认词表

乐鑫提供独立的 `mn7_cn` 和 `mn7_en` 模型，框架支持模型切换，官方不支持中英文混合命令。[官方 MultiNet 说明](https://docs.espressif.com/projects/esp-sr/en/latest/esp32s3/speech_command_recognition/README.html)

本项目通过构建脚本选择一种语言。中英文写入的是**同一个 `model` 分区的不同内容**，不是设备同时运行两套模型；切换语言必须让应用与模型镜像匹配。当前 0.5.0 的硬件验证只覆盖中文，未验收英文效果。

```powershell
python tools\flash_variant.py zh --build-only
python tools\flash_variant.py en --build-only
```

即使带 `--build-only`，脚本仍会修改项目语言配置和生成头文件，不是只读检查。不要把这两行当作日常查询命令连续执行。默认词表来自 `tools/commands_zh.json` / `tools/commands_en.json`；不要直接编辑 `src/generated_multinet_commands.h`。

若希望新烧录且没有 NVS 自定义词的设备也默认使用“旺财旺财／小火小火”，需另外修改中文预设 JSON 并重新生成、编译、烧录。本次改变的是现有设备 NVS，未改变编译默认表。

### 6.6 识别调节与排查

```json
{"cmd":"get_recognition"}
{"cmd":"set_recognition","min_score":0.05,"cooldown_ms":1000}
{"cmd":"set_recognition","min_score":0.02}
```

`get_recognition` 与 `get_audio_diagnostics` 均返回音频及识别诊断。`min_score` 范围 0.02～1，`cooldown_ms` 为 200～3000 的整数；省略字段不变。设置不写 NVS，重启恢复默认。`threshold` 和 160 ms 确认窗口不能通过这些字段修改。

误识别可能涉及音频输入、发音、模型判别能力和候选确认策略，不能仅凭低分断言模型精度不足。先确认实际通道与电平，记录带逐句标签的音频和事件，再比较单一参数。VAD 降敏不等于识别门槛提高，不应用修改 `set_vad` 来代替识别效果测试。

## 7. 扬声器与回声消除（AEC）

### 7.1 AEC 必须具备的参考链路

播放链路：

```text
电脑播放器
  → ReSpeaker Flex USB 扬声器端点
  → ESP32 UAC 播放环形缓冲
  → 左右声道下混为单声道
  → I2S TX
  → XVF3800 / 功放 / 匹配的扬声器
  └─→ XVF3800 AEC 数字远端参考
```

只有声音通过 ReSpeaker Flex 的 USB 扬声器端点播放，AEC 才能获得与实际扬声器内容对应的数字参考。如果音乐从电脑其他声卡或蓝牙音箱播放，Flex 无法获得这一路数字参考，消除效果会明显下降。

### 7.2 正常状态判断

通过 `get_status` 检查：

- `aec_valid=true`：AEC 状态有效；
- `aec_bypass=0`：没有旁路；
- `aec_farends=1`：存在一路远端参考；
- `aec_ref=1`：参考信号已进入处理链；
- `aec_converged=1`：自适应滤波器已经收敛；
- `i2s_inactive=0`：I2S 音频循环正常；
- `nla=1`：非线性残余回声抑制已启用。

AEC 可以显著压低回声，但不保证录音数学意义上完全静音。保留少量残余属于正常现象。

### 7.3 为什么碰到阵列后回声会突然变差

AEC 会学习“扬声器数字参考经过空气、桌面、外壳后到达麦克风”的声学路径。移动阵列、触碰外壳、改变扬声器相对位置会使这条路径瞬间变化，原来的自适应滤波器暂时失配，需要重新收敛。

同时，触碰产生的是结构振动和摩擦噪声，它不在数字播放参考中，AEC 无法按普通扬声器回声抵消。因此常见现象是：触碰时残余回声明显增加，设备稳定数秒后效果恢复。

测试时应固定阵列和扬声器，避免线缆拉动外壳，并等待 AEC 重新收敛后再比较。

### 7.4 最大音量时回声仍较大

最大音量容易产生以下非线性成分：

- 功放削波；
- 扬声器单元失真；
- 外壳、桌面和线缆共振；
- 电源压降或供电噪声。

线性 AEC 最擅长消除与数字参考线性相关的回声，无法完全预测上述非线性失真。`nla=1` 会继续抑制残余，但也不能保证全消除。建议把系统音量降到扬声器不失真的范围，再评估 AEC。

条件式麦克风增益不是固定 `+8 dB`，仅在人声有效且输入较小时生效；但任何后级放大都会使残余回声听起来更明显，因此比较 AEC 时应同时观察 `raw_rms` 和 `rms`。

### 7.5 AEC 延迟调试

当前编译默认值：

```c
#define AEC_SYS_DELAY_SAMPLES 0
```

在线设置范围为 `-256～256` 个采样点：

```json
{"cmd":"set_aec_delay","samples":0}
```

设备会先返回排队响应，实际应用后再返回完成响应。随后用 `get_status` 检查 `aec_delay`。在线值仅在本次运行期间有效，重启后恢复 `src/board_config.h` 的编译默认值。

延迟应使用同步的“播放原音 + 麦克风录音”做相关性测量，不建议只凭听感来回修改。

### 7.6 切歌时出现尾音重复

切歌可能涉及播放器暂停、关闭或重新打开 UAC 流。当前固件播放路径预填静音，并用线性插值做小幅时钟补偿；不能直接将异常尾音解释为“固件一直重复上一帧”，应结合波形和计数定位。

排查时：

1. 查询 `speaker_underrun` / `speaker_overflow` 的增量；`uac_underrun` 属于麦克风方向；
2. 测试另一个能保持音频设备连续打开的播放器；
3. 检查 USB 设备是否发生重枚举；
4. 主机在无新音频时应连续发送静音，而不是让流突然断开；
5. 检查 `src/usb_uac_device.c` 的播放缓冲与 `src/i2s_audio.c` 的 I2S TX。

0.5.0 已做静音播放和录音并行检查，这不等于已经验证切歌或有声 AEC 效果。

## 8. 修改原理、声源方向与验证

### 8.1 USB 与协议

`src/usb_descriptors.c` 定义 UAC2 麦克风、UAC2 扬声器和 CDC ACM 的复合设备描述符。`src/usb_uac_device.c` 处理音频缓冲、USB 流、音量/静音控制和漂移补偿。

`src/usb_cdc_channel.c` 用独立命令与发送任务处理 JSON、词表 CRUD、VAD 参数、运行诊断和事件序列化，避免在 USB 回调里执行耗时的模型更新或 NVS 写入。

### 8.2 音频处理与识别模块

| 文件 | 当前职责 |
|---|---|
| `src/board_config.h` | 引脚、I2S 格式、默认时隙、人声 VAD 与 AEC 默认值 |
| `src/i2s_audio.c` | 两通道采集、人声 VAD、两种活动门控、增益分发；I2S 播放 |
| `src/audio_processing.h/.c` | VAD 档位、底噪、增益、峰值保护和线性重采样 |
| `src/pcm_stream.c` | 20 ms 音频帧到模型输入块的连续拼接 |
| `src/esp_sr_multinet_backend.c` | 模型加载、词表注册、NVS、候选结果及解码重置 |
| `src/recognition_catalog.h/.c` | 词表规范化、冲突检查和两类词条 ID 映射 |
| `src/wake_candidate.c` | 160 ms 确认窗口内保留最高候选得分 |
| `src/multinet_engine.c` | 队列、过期帧、历史快照、事件过滤及每词冷却 |
| `src/xmos_i2c.c` | XMOS 配置、路由、AEC 和方向读取 |
| `src/doa_tracker.c` / `app_state.c` | 圆周方向跟踪、有效性、过期和状态快照 |
| `src/main.c` | 初始化、XMOS 轮询/恢复及 VAD 事件上报 |

### 8.3 词表分类与保存

原有唤醒词 NVS 格式保留；普通关键字使用独立键。注册模型时，唤醒词内部编号占 1～3，普通关键字占 4～19；模型结果再映射回用户 ID、名称与事件类型。

外部 `wake/id=1` 与 `keyword/id=1` 因此可以共存。去重按内部词条区分；词表修改会重置解码和去重上下文。模型更新或保存失败会尝试回退；回退也失败则将识别器标记为不可用，避免使用不一致词表。

`tools/flash_variant.py` 负责语言和编译默认表，不负责导出当前 NVS。复制项目源码不会复制设备当前保存的两组词表。

### 8.4 三类事件的声源角度

测试应围绕**通过 FPC 连接的圆形四麦小板**改变说话位置，不是围绕 XIAO 或主板。

方向由 XMOS 提供，ESP32 检查样本及音频活动、转换角度并处理 359°/0° 跨界、短时跳变与过期。没有新的有效方向约 600 ms 后，状态返回 `doa=-1,doa_valid=false`；读取失败不会把旧角度当成新样本刷新。

| 字段 | 使用规则 |
|---|---|
| `doa` | 0～359°；无效为 -1 |
| `doa_valid` | 显示或用于控制前先检查此字段 |
| `doa_age_ms` | 方向样本年龄；尚未获得样本时为 -1 |
| `doa_source` | 当前来源，例如 `auto`；无效为 `none` |
| `audio_age_ms` | 识别结果对应音频帧到事件入队的时间，VAD 事件无此字段 |

`wake` / `keyword` 的角度取最高得分音频帧附近的历史快照，避免识别结束时人已换位置。其 `doa_age_ms` 是快照时的年龄，事件入队时方向总年龄约为 `doa_age_ms + audio_age_ms`。VAD 使用当前状态，并在活动期间按需更新。

角度字段有效不等于已经完成精度标定。零度方向与阵列安装有关，应在固定方位实测建立坐标；非人声或静音不保证有有效方向。

```json
{"cmd":"get_doa"}
```

该诊断返回 XMOS 版本、麦克风数量、阵列类型、固定波束、实际路由和读取错误。角度持续不变时先核对阵列类型、`doa_valid`、`doa_age_ms` 和错误计数，不要用固定偏移掩盖数据失效。

### 8.5 修改后如何验证

本地回归测试（需 Visual Studio C++ 构建工具）：

```powershell
powershell -ExecutionPolicy Bypass -File tools\test_native.ps1
```

硬件诊断（需 `pyserial numpy sounddevice`）：

```powershell
python tools\verify_firmware.py --seconds 100 --check-protocol --output artifacts\verification.jsonl
```

工具期望固件 0.5.0，默认边录入 UAC 数据边播放静音，保存事件和音频统计，**不保存 PCM 录音**。它不修改词表，不能自动判定用户实际说了什么。

真人测试应固定距离和方位，标注每次口令及时间，同时包含静音、无关讲话和背景声。统计每词命中、漏检、串词及无口令误触发；角度测试需标注真实方位。要复现误识别，应另存同步 PCM 与候选轨迹。

长时间使用还应验证持续录音、重连、有声播放/AEC、暂停与切歌。自动回归不能替代这些场景测试。

### 8.6 已完成的验证与未解决项

2026-10-14 的 0.5.0 记录在 `artifacts/keywords_vad/`：

- 编译、本机算法回归和烧录哈希校验通过。
- 普通关键字 CRUD、16 条容量、参数边界、冲突与失败回退通过硬件检查。
- 两个临时关键字在物理复位后保留，持久化验证通过；测试后已清除。
- 100 秒 UAC 输入与静音输出并行期间，未检测到重启、JSON 解析错误或音频队列丢帧；音频和串口相关错误计数未增加。
- 收到 4 个 wake、6 个 keyword，10 个识别事件都带有效方向；keyword 出现 342°、51°、197°、172°，已不固定在同一角度。
- VAD 收到 13 个 start、12 个 end、42 个 update，其中 30 个事件有有效方向。采集结束时仍有活动，不能要求窗口内边界数量完全相等。
- **识别准确率验收未通过**：旧词表出现 3 次“二师兄”，用户确认没说过。代码复核未发现编号错位，但误识别根因未确定。旧日志门槛重筛到 0.052 可挡住这 3 次误报，也会过滤唯一的“孙悟空”和 6 个 keyword 中的 3 个，因此未据此提高默认门槛。重筛不等于固件重测。
- 未量化现场 VAD 误报率或角度误差；静音播放测试也不能验证有声 AEC 效果。

之后通过协议改为“旺财旺财／小火小火”，写入和读回成功，记录在 `artifacts/wake_words_20261014_221102/`。两条新词尚无真人准确率结果，旧词测试不能作为新词效果证明。

## 9. 构建、烧录与迁移

### 9.1 环境依赖

- Python 3；
- PlatformIO Core 或 VS Code PlatformIO 扩展；
- `pyserial`，用于脚本发现端口和 CDC 监视；
- 支持数据传输的 USB 线。

安装命令：

```powershell
python -m pip install --upgrade platformio pyserial
```

ESP-IDF、TinyUSB、ESP-SR 等组件由 PlatformIO/项目依赖配置下载。首次构建需要联网，之后通常可以使用本地缓存。

### 9.2 推荐烧录方式

中文：

```powershell
python tools\flash_variant.py zh
```

英文：

```powershell
python tools\flash_variant.py en
```

只构建不烧录：

```powershell
python tools\flash_variant.py zh --build-only
python tools\flash_variant.py en --build-only
```

指定下载端口：

```powershell
python tools\flash_variant.py zh --port COM12
```

如果脚本提示进入下载模式：

1. 按住 **XIAO ESP32S3 小板**的 `BOOT`，不是 Flex 主板 SafeMode；
2. 短按 XIAO 的 `RESET`；
3. 松开 `BOOT`；
4. 等待出现 `303A:1001` 下载端口；
5. 回到终端按 Enter；
6. 烧录完成后短按 `RESET` 返回应用模式。

### 9.3 手动 PlatformIO 命令

```powershell
pio run -e seeed_xiao_esp32s3 -j 2
pio run -e seeed_xiao_esp32s3 -t srmodels
pio run -e seeed_xiao_esp32s3 -t upload --upload-port COM12
```

首次烧录或更换模型时，应配套生成和写入应用与 `srmodels.bin`。本项目 PlatformIO 上传钩子包含模型镜像，切换语言可使用 `flash_variant.py`。

如果语言、模型和分区布局均未改变，只升级应用可仅将 `firmware.bin` 写入当前应用偏移 `0x10000`，保留模型与 NVS。当前模型分区位于 `0x310000`、容量 3 MB，以 `partitions.csv` 为准；不要对已配置的设备整片擦除。修改运行时词表不需要烧录。

### 9.4 迁移到其他电脑或项目

最稳妥的方法是复制整个 `flex_xiao` 文件夹，至少保留：

```text
platformio.ini
CMakeLists.txt
partitions.csv
dependencies.lock
sdkconfig.defaults
sdkconfig.seeed_xiao_esp32s3
src/
include/
lib/
tools/
test/
SERIAL_PROTOCOL.md
README.md
```

关键文件：

| 文件 | 用途 |
|---|---|
| `src/board_config.h` | 引脚、音频、人声 VAD 和 AEC 默认值 |
| `src/audio_processing.h/.c` | VAD 事件档位、底噪、增益和重采样 |
| `src/i2s_audio.c` | 采集、播放、RMS、VAD、增益、音频分发 |
| `src/usb_descriptors.c` | USB 复合设备描述符 |
| `src/usb_uac_device.c` | UAC 流与缓冲 |
| `src/usb_cdc_channel.c` | CDC JSON 协议 |
| `src/esp_sr_multinet_backend.c` | MultiNet、两类词表与 NVS |
| `src/recognition_catalog.h/.c` | 词表校验和编号映射 |
| `src/multinet_engine.c` | 音频帧队列、过滤、去重和事件发送 |
| `tools/flash_variant.py` | 中英文构建和烧录 |
| `tools/serial_monitor.py` | 串口监视与控制 |
| `tools/commands_zh.json` | 中文默认命令词 |
| `tools/commands_en.json` | 英文默认命令词 |
| `SERIAL_PROTOCOL.md` | 完整 CDC 协议 |

如果是把功能合并到一个已有 PlatformIO 项目，不能只复制某个 `.c` 文件。还需要合并 `platformio.ini`、ESP-IDF 组件依赖、分区表、SDK 配置、TinyUSB 描述符和模型生成流程。

`dependencies.lock` 记录组件版本，`.pio` 缓存可在新电脑重建。**复制源码不会复制设备 NVS**：需另行导出 `get_commands`、分页 `get_keywords`，在目标设备通过协议恢复。当前两条唤醒词恢复命令见第 6.3 节。

项目内 `tools/build_microphone_manual.py` 使用独立的旧 DOCX 内容，未随这份 Markdown 自动同步；其生成结果不能当作本文更新版。

## 10. 常见问题

### 10.1 Windows 输入设备一直出现和消失

这通常说明设备在反复复位或 USB 连接不稳定：

- 更换可靠的数据线和电脑 USB 端口；
- 尽量直连电脑，避免不稳定的 Hub；
- 确认 `BOOT` 键没有卡住；
- 烧录后短按 `RESET`，确认退出下载模式；
- 暂时断开可能造成供电波动的外设；
- 查看 UART 日志是否存在重启、看门狗或崩溃。

### 10.2 CDC 端口不刷新，但声音输入设备刷新

UAC 和 CDC 属于同一个复合设备，但 Windows 对各接口的重建时序不同。声音设备反复消失仍说明整个应用 USB 设备可能在重枚举；应从供电、线缆、复位日志和固件崩溃排查，而不是只看 COM 端口。

### 10.3 录音声音小

- 确认录音软件选中了 ReSpeaker Flex；
- 关闭 Windows 自动增益和音频增强后重新比较；
- 查看 `raw_rms`、`rms` 与 `speech`；
- 确认讲话距离与方向合理；
- 检查是否只有 `raw_rms` 很低，或 `speech` 始终不成立；
- 不要直接把最大软件增益继续拉高，过大增益会增加削波、底噪和残余回声。

### 10.4 隔一段时间出现噗呲或电流声

- 连续查询 `uac_underrun`、`uac_overflow` 是否增长；
- 更换 USB 线和端口；
- 检查 I2S BCLK、WS、DATA、GND；
- 避免扬声器功放、电源线和麦克风信号线互相干扰；
- 关闭主机音频增强，录制原始 PCM 对照；
- 若与固定时间间隔相关，检查任务阻塞和缓冲水位。

### 10.5 串口平时没有输出

这是正常设计。设备只在以下情况发送数据：

- `vad` 开始、结束，或活动期间方向有效性/角度变化；
- 唤醒词或普通关键字识别成功；
- 主机发来请求；
- 发生需要上报的错误。

需要主动查看状态时发送 `{"cmd":"get_status"}`。

### 10.6 COM 端口被占用

关闭其他串口监视器、PlatformIO Monitor 和仍在运行的 Python 进程。同一时间通常只能由一个程序打开 Windows COM 端口。

### 10.7 只有下载端口，没有麦克风和扬声器

设备仍处于 `303A:1001` 下载模式。烧录完成后短按 `RESET`。若仍不能进入应用模式，检查应用是否成功烧录以及启动日志中是否崩溃。

### 10.8 AEC 效果不好

按以下顺序检查：

1. 播放设备必须选择 ReSpeaker Flex；
2. `aec_ref=1`、`aec_bypass=0`、`aec_converged=1`；
3. 阵列和扬声器固定不动；
4. 扬声器没有削波或结构共振；
5. 播放开始后等待数秒重新收敛；
6. 关闭主机录音增强；
7. 使用同步录音测量系统延迟，而不是只凭听感调参。

## 11. 快速命令表

以下按用途选择执行，不是需要从上到下全部运行的步骤。中英文构建烧录二选一；日常查看只需查询或监视命令。所有项目脚本均从 `flex_xiao` 项目目录运行。

```powershell
# 安装工具
python -m pip install --upgrade platformio pyserial

# 中文：构建并烧录
python tools\flash_variant.py zh

# 英文：构建并烧录
python tools\flash_variant.py en

# 查看端口
pio device list

# 测试 CDC
python tools\serial_monitor.py COM15 --ping

# 查询状态
python tools\serial_monitor.py COM15 --status

# 持续监视 vad / wake / keyword / error
python tools\serial_monitor.py COM15

# 查询运行时命令词
python tools\serial_monitor.py COM15 --list-commands

# 恢复当前两条唤醒词（会替换整个唤醒词表）
python tools\serial_monitor.py COM15 --set-commands `
  --command 1 "wang cai wang cai" "旺财旺财" `
  --command 2 "xiao huo xiao huo" "小火小火"
```

其他串口 JSON 查询（每条以换行结束）：

```json
{"cmd":"get_keywords","offset":0,"limit":2}
{"cmd":"get_vad"}
{"cmd":"get_audio_diagnostics"}
{"cmd":"get_runtime"}
{"cmd":"get_doa"}
```

需要降低 VAD 灵敏度时发送 `{"cmd":"set_vad","sensitivity":"low"}`，仅本次启动有效。`reset_commands` 会恢复固件内置旧词，`clear_keywords` 会清空普通关键字；二者都是写操作，不应当作查询命令执行。

## 12. 主机集成检查清单

- [ ] 根据 VID/PID/产品名发现应用 CDC，不写死下载端口。
- [ ] CDC 按 UTF-8 JSON Lines 分帧。
- [ ] 打开端口后设置 DTR/RTS，等待设备稳定并发送 `ping`。
- [ ] 按 `type` 分发异步事件与命令响应。
- [ ] 用 `(type,id)` 区分唤醒词与普通关键字。
- [ ] 不要求先收到 `vad/start` 才接受 `wake` / `keyword`。
- [ ] `vad/update` 只更新方向，不创建新片段。
- [ ] 检查 `doa_valid`，不把 -1 或过期方向当成当前位置。
- [ ] UAC PCM 接收与 CDC 读取分别运行，不互相阻塞。
- [ ] 为 YAMNet 保留 `300～500 ms` 预录音。
- [ ] 不用 `speech` 丢弃非人声 `vad` 片段。
- [ ] 保存 `score`、`threshold`、`min_score`，不把 `confidence` 当正确率。
- [ ] 按 `next_offset` 分页读取完整关键字表，不把修改响应第一页当完整表。
- [ ] 区分 NVS 持久词表与重启失效的 VAD/识别参数。
- [ ] 用口令标注统计命中、漏检和误触发，不凭事件数量宣称准确率。
- [ ] 接受 CDC 异步事件可能丢失，不依赖事件重放。
- [ ] USB 重枚举后重新发现端口并恢复状态机。
- [ ] 切换中英文时同时烧录应用固件和对应模型分区。

---

更完整的 CDC 字段定义、命令对象规则、错误码和 Python 主机示例，请阅读项目根目录的 `SERIAL_PROTOCOL.md`。固件参数应以当前源代码为最终依据。

0.5.0 实测报告在 `artifacts/keywords_vad/verification_report.txt`，最新唤醒词写入记录在 `artifacts/wake_words_20261014_221102/after.json`。本次仅更新说明文档，没有重新构建或烧录设备。
