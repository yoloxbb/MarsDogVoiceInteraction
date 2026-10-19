# ReSpeaker JSON 声源事件

Voice 直接判断 ReSpeaker CDC JSON，在 /perception/audio_event 发布
`EVT_VOICE_SOUND_SOURCE_DETECTED`。

## 判断条件

```python
message.get("type") == "vad"
and message.get("state") == "update"
and message.get("doa_valid") is True
```

以下输入触发声源事件：

```json
{"type":"vad","source":"audio_activity","state":"update","active":true,"doa":95,"doa_valid":true,"doa_age_ms":30,"doa_source":"auto","raw_rms":0.018,"noise_floor":0.0006,"speech":0}
```

`doa_valid=false` 不发布，字符串 `"true"/"false"` 也不按 JSON 布尔值处理；
`start/end` 不触发。每条满足条件的 `update` 各发布一次，不要求先收到 `start`，
不等待 `end`、录音截取或 YAMNet 分类。额外校验角度为有限的 `[0,360)` 数值，
方向年龄非负，方向年龄加截至发布的全部主机等待时间不超过 600 ms，避免旧角度触发行为。
发布前再次检查时效；`doa_age_ms` 和 `header.stamp` 对应同一发布时间，
`sound_trigger_stamp` 始终保留原始更新接收时间。

设备这类 JSON 没有具体声音类别；本事件表示“检测到有有效方向的声音活动”。
`speech=0` 的非人声同样允许通过。当前事件不使用声音类别白名单。

## 发布字段

| 字段 | 含义 |
|---|---|
| `event_type` | `EVT_VOICE_SOUND_SOURCE_DETECTED` |
| `sound_id` | 此次有效更新事件的唯一 ID，用于下游去重 |
| `sound_trigger_stamp` | CDC 更新的接收时间，Unix 秒 |
| `doa/doa_valid/doa_age_ms/doa_source` | 设备原始角度、有效性、截至发布的方向年龄、来源 |
| `header.stamp/header.frame_id` | 发布时间 / `microphone_array` |
| `intent_source/dispatch_role` | `respeaker_vad/sound_source` |
| `should_trigger_behavior_tree/is_executable` | `true/false`；交给 Tree 路由通用序列 |
| `slots` | `trigger=vad/update`，及原始 JSON 中存在的 `source/raw_rms/rms/noise_floor/speech/xmos_speech` |

`interaction_id/wake_id/utterance_id` 为空，本事件独立于语音唤醒和主人身份。
下游以 `sound_id` 去重，结合发布时间和 `doa_age_ms` 检查时效，使用 `doa` 读取方位。
安装标定由动作系统应用一次。事件只含方向，没有距离；接入“转向 → 靠近 →
人脸/物体/姿态识别”的通用序列需要在 Tree 增加此事件的路由，并由视觉等提供测距反馈。

## 配置与监听

`config/voice.respeaker.yaml`：

```yaml
sound_source:
  enabled: true
  max_doa_age_ms: 600.0
```

使用现有 `respeaker_serial/respeaker_sherpa` 的同一个 CDC reader，无新增模型或依赖。
CDC VAD 和 wake 使用不同的有界队列；重连时清空旧消息。ROS 定时器每 50 ms 轮询，
符合条件就发布，不增加片段等待。运行日志记录 `sound_id/doa/doa_valid/doa_age_ms`。

加载更新代码和配置并重启 Voice 后监听：

```bash
ros2 topic echo /perception/audio_event --field data --filter '"EVT_VOICE_SOUND_SOURCE_DETECTED" in m.data'
```

离线测试覆盖有效 update 直接触发、false/错误类型不触发、start/end 不触发、
无 start 的 update、角度/时间有效性、每条更新一次、断线清队列和待机时发布。
真实 CDC/ROS 联调及 Tree 通用序列仍需现场验证。
