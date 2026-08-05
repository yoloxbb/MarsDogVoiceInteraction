# 迁移说明

来源为 `20260622_MarsDogPro/marsdog_perception` 当前工作区状态。

- 讯飞唤醒 Adapter → 语音项目
- VAD、ASR、Speaker、Intent Provider → 语音项目
- `PerceptionStateMachine` → 语音会话状态机
- `EnrollmentManager` → 仅保留 `SpeakerEnrollmentManager`
- `PerceptionBridgeNode` → 仅提取语音轮询、识别链和语音任务处理
- 原 `registry.json` → 独立 `speaker_registry.json`

语音链已移除对 `latest_frame`、人脸 Provider 和进程内 `TargetManager` 的访问。
跨模态 `target_tracker` 应作为下游 ROS2 消费者继续维护。
