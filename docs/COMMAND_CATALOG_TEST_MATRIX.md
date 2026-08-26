# 确定性词库测试对齐表

本文档供测试人员逐条对齐 ASR 词库输入与最终语音事件。它是
[`config/command_catalog.yaml`](../config/command_catalog.yaml) 的可读测试快照；若两者不一致，
`command_catalog.yaml` 是唯一权威源。

## 1. 当前目录口径

- 目录版本：`2026-08-25-full`。
- 源数据：`116` 条；事件路由：`81` 个；运行时中文短语：`155` 条。
- 核心训练指令：`19` 组，对应表中“核心=是”的所有短语。
- 表中每条中文词/句都必须独立测试；同一 `command_key` 下的短语是等价入口。
- 英文 `reference_phrases_en` 当前只是参考元数据，不参与确定性词库匹配，因此不列入本表验收。
- `BT=是` 只表示 Voice 应设置 `should_trigger_behavior_tree=true`，不代表下游已经完成事件映射或动作执行。
- `action_name=—` 表示产品目录没有给出独立 `ACT_*`，但仍必须发布表中的 `event_type`。

## 2. 单条用例判定

每条用例至少满足：

1. `speech.asr_text` 与播放文本一致，或符合用例事先允许的等价转写。
2. 同一个 `utterance_id` 出现 `stage_complete stage=command_lexicon result=matched`。
3. 日志中的 `command_key/command_id/event_type/emotion/control` 与本表一致。
4. 最终 `event_publish.intent_source=command_lexicon`；目录命中后不再进入 `stage=intent`。
5. 如果同一句已由 KWS 提前发布相同事件，允许目录结果为 `suppressed_duplicate`，但不允许发布两个动作事件。

说明：`PRAISE`、`SCOLD` 为非执行社交事件，`BT=否`；其他当前目录项为 `control=DO`，`BT=是`。

## 3. 词/句与事件逐条对齐表

| 用例 ID | 核心 | 源行 | 中文词/句 | `command_key` | `command_id` | 期望 `event_type` | `emotion` | `control` | `action_name` | BT |
|---|:---:|---:|---|---|---|---|---|---|---|:---:|
| `CAT-001` | 是 | 1 | `走` | `WALK` | `CMD_WALK` | `EVT_VOICE_COMMAND_WALK` | `NONE` | `DO` | `ACT_WALK` | 是 |
| `CAT-002` | 是 | 1 | `去` | `WALK` | `CMD_WALK` | `EVT_VOICE_COMMAND_WALK` | `NONE` | `DO` | `ACT_WALK` | 是 |
| `CAT-003` | 是 | 2 | `过来` | `COME` | `CMD_COME_HERE` | `EVT_VOICE_COMMAND_COME` | `NONE` | `DO` | `ACT_COME` | 是 |
| `CAT-004` | 是 | 2 | `来` | `COME` | `CMD_COME_HERE` | `EVT_VOICE_COMMAND_COME` | `NONE` | `DO` | `ACT_COME` | 是 |
| `CAT-005` | 是 | 2 | `回来` | `COME` | `CMD_COME_HERE` | `EVT_VOICE_COMMAND_COME` | `NONE` | `DO` | `ACT_COME` | 是 |
| `CAT-006` | 是 | 2 | `来这` | `COME` | `CMD_COME_HERE` | `EVT_VOICE_COMMAND_COME` | `NONE` | `DO` | `ACT_COME` | 是 |
| `CAT-007` | 是 | 2 | `到我这儿来` | `COME` | `CMD_COME_HERE` | `EVT_VOICE_COMMAND_COME` | `NONE` | `DO` | `ACT_COME` | 是 |
| `CAT-008` | 是 | 3 | `跟着我` | `FOLLOW` | `CMD_FOLLOW` | `EVT_VOICE_COMMAND_FOLLOW` | `NONE` | `DO` | `ACT_GO_OUT_TO_PLAY` | 是 |
| `CAT-009` | 是 | 3 | `跟我走` | `FOLLOW` | `CMD_FOLLOW` | `EVT_VOICE_COMMAND_FOLLOW` | `NONE` | `DO` | `ACT_GO_OUT_TO_PLAY` | 是 |
| `CAT-010` | 是 | 4 | `出去玩` | `GO_OUT` | `CMD_GO_OUT` | `EVT_VOICE_COMMAND_GO_OUT` | `NONE` | `DO` | — | 是 |
| `CAT-011` | 是 | 4 | `出去溜溜` | `GO_OUT` | `CMD_GO_OUT` | `EVT_VOICE_COMMAND_GO_OUT` | `NONE` | `DO` | — | 是 |
| `CAT-012` | 是 | 5 | `回家` | `GO_HOME` | `CMD_GO_HOME` | `EVT_VOICE_COMMAND_GO_HOME` | `NONE` | `DO` | — | 是 |
| `CAT-013` | 是 | 6 | `靠近点` | `APPROACH` | `CMD_APPROACH` | `EVT_VOICE_COMMAND_APPROACH` | `NONE` | `DO` | `ACT_COME_CLOSER` | 是 |
| `CAT-014` | 是 | 7 | `退后` | `BACK_UP` | `CMD_BACK_UP` | `EVT_VOICE_COMMAND_BACK_UP` | `NONE` | `DO` | `ACT_BACK_UP` | 是 |
| `CAT-015` | 是 | 8 | `坐` | `SIT` | `CMD_SIT` | `EVT_VOICE_COMMAND_SIT` | `NONE` | `DO` | `ACT_SIT` | 是 |
| `CAT-016` | 是 | 8 | `坐下` | `SIT` | `CMD_SIT` | `EVT_VOICE_COMMAND_SIT` | `NONE` | `DO` | `ACT_SIT` | 是 |
| `CAT-017` | 是 | 8 | `蹲下` | `SIT` | `CMD_SIT` | `EVT_VOICE_COMMAND_SIT` | `NONE` | `DO` | `ACT_SIT` | 是 |
| `CAT-018` | 是 | 9 | `趴下` | `LIE_DOWN` | `CMD_LIE_DOWN` | `EVT_VOICE_COMMAND_LIE_DOWN` | `NONE` | `DO` | `ACT_LIE_DOWN` | 是 |
| `CAT-019` | 是 | 9 | `躺下` | `LIE_DOWN` | `CMD_LIE_DOWN` | `EVT_VOICE_COMMAND_LIE_DOWN` | `NONE` | `DO` | `ACT_LIE_DOWN` | 是 |
| `CAT-020` | 是 | 10 | `biu` | `PLAY_DEAD` | `CMD_DEAD` | `EVT_VOICE_COMMAND_PLAY_DEAD` | `NONE` | `DO` | `ACT_PLAY_DEAD` | 是 |
| `CAT-021` | 是 | 10 | `装死` | `PLAY_DEAD` | `CMD_DEAD` | `EVT_VOICE_COMMAND_PLAY_DEAD` | `NONE` | `DO` | `ACT_PLAY_DEAD` | 是 |
| `CAT-022` | 是 | 11 | `起来` | `STAND_UP` | `CMD_STAND_UP` | `EVT_VOICE_COMMAND_STAND_UP` | `NONE` | `DO` | `ACT_STAND_UP` | 是 |
| `CAT-023` | 是 | 11 | `站起来` | `STAND_UP` | `CMD_STAND_UP` | `EVT_VOICE_COMMAND_STAND_UP` | `NONE` | `DO` | `ACT_STAND_UP` | 是 |
| `CAT-024` | 是 | 12 | `站好` | `STAND_STILL` | `CMD_STAND_STILL` | `EVT_VOICE_COMMAND_STAND_STILL` | `NONE` | `DO` | `ACT_STAND_STILL` | 是 |
| `CAT-025` | 是 | 12 | `站着` | `STAND_STILL` | `CMD_STAND_STILL` | `EVT_VOICE_COMMAND_STAND_STILL` | `NONE` | `DO` | `ACT_STAND_STILL` | 是 |
| `CAT-026` | 是 | 13 | `握手` | `SHAKE_HAND` | `CMD_HAND` | `EVT_VOICE_COMMAND_SHAKE_HAND` | `NONE` | `DO` | `ACT_SHAKE_HAND` | 是 |
| `CAT-027` | 是 | 13 | `握个手` | `SHAKE_HAND` | `CMD_HAND` | `EVT_VOICE_COMMAND_SHAKE_HAND` | `NONE` | `DO` | `ACT_SHAKE_HAND` | 是 |
| `CAT-028` | 是 | 13 | `抬手` | `SHAKE_HAND` | `CMD_HAND` | `EVT_VOICE_COMMAND_SHAKE_HAND` | `NONE` | `DO` | `ACT_SHAKE_HAND` | 是 |
| `CAT-029` | 是 | 14 | `击掌` | `HIGH_FIVE` | `CMD_FIVE` | `EVT_VOICE_COMMAND_HIGH_FIVE` | `NONE` | `DO` | `ACT_HIGH_FIVE` | 是 |
| `CAT-030` | 是 | 14 | `击个掌` | `HIGH_FIVE` | `CMD_FIVE` | `EVT_VOICE_COMMAND_HIGH_FIVE` | `NONE` | `DO` | `ACT_HIGH_FIVE` | 是 |
| `CAT-031` | 是 | 14 | `拍手` | `HIGH_FIVE` | `CMD_FIVE` | `EVT_VOICE_COMMAND_HIGH_FIVE` | `NONE` | `DO` | `ACT_HIGH_FIVE` | 是 |
| `CAT-032` | 是 | 15 | `转圈` | `SPIN` | `CMD_SPIN` | `EVT_VOICE_COMMAND_SPIN` | `NONE` | `DO` | `ACT_SPIN` | 是 |
| `CAT-033` | 是 | 16 | `翻滚` | `ROLL_OVER` | `CMD_ROLL` | `EVT_VOICE_COMMAND_ROLL_OVER` | `NONE` | `DO` | `ACT_ROLL_OVER` | 是 |
| `CAT-034` | 是 | 17 | `别动` | `HOLD_POSITION` | `CMD_HOLD_POSITION` | `EVT_VOICE_COMMAND_HOLD_POSITION` | `NONE` | `DO` | `ACT_STOP` | 是 |
| `CAT-035` | 是 | 17 | `等着` | `HOLD_POSITION` | `CMD_HOLD_POSITION` | `EVT_VOICE_COMMAND_HOLD_POSITION` | `NONE` | `DO` | `ACT_STOP` | 是 |
| `CAT-036` | 是 | 17 | `停` | `HOLD_POSITION` | `CMD_HOLD_POSITION` | `EVT_VOICE_COMMAND_HOLD_POSITION` | `NONE` | `DO` | `ACT_STOP` | 是 |
| `CAT-037` | 是 | 17 | `不准动` | `HOLD_POSITION` | `CMD_HOLD_POSITION` | `EVT_VOICE_COMMAND_HOLD_POSITION` | `NONE` | `DO` | `ACT_STOP` | 是 |
| `CAT-038` | 是 | 17 | `不许动` | `HOLD_POSITION` | `CMD_HOLD_POSITION` | `EVT_VOICE_COMMAND_HOLD_POSITION` | `NONE` | `DO` | `ACT_STOP` | 是 |
| `CAT-039` | 是 | 17 | `老实点` | `HOLD_POSITION` | `CMD_HOLD_POSITION` | `EVT_VOICE_COMMAND_HOLD_POSITION` | `NONE` | `DO` | `ACT_STOP` | 是 |
| `CAT-040` | 是 | 17 | `等等` | `HOLD_POSITION` | `CMD_HOLD_POSITION` | `EVT_VOICE_COMMAND_HOLD_POSITION` | `NONE` | `DO` | `ACT_STOP` | 是 |
| `CAT-041` | 是 | 18 | `放下` | `DROP` | `CMD_SPIT` | `EVT_VOICE_COMMAND_DROP` | `NONE` | `DO` | `ACT_DROP` | 是 |
| `CAT-042` | 是 | 18 | `松开` | `DROP` | `CMD_SPIT` | `EVT_VOICE_COMMAND_DROP` | `NONE` | `DO` | `ACT_DROP` | 是 |
| `CAT-043` | 是 | 18 | `松口` | `DROP` | `CMD_SPIT` | `EVT_VOICE_COMMAND_DROP` | `NONE` | `DO` | `ACT_DROP` | 是 |
| `CAT-044` | 是 | 18 | `张嘴` | `DROP` | `CMD_SPIT` | `EVT_VOICE_COMMAND_DROP` | `NONE` | `DO` | `ACT_DROP` | 是 |
| `CAT-045` | 是 | 19 | `安静` | `QUIET` | `CMD_QUIET` | `EVT_VOICE_COMMAND_QUIET` | `NONE` | `DO` | `ACT_QUIET` | 是 |
| `CAT-046` | 是 | 19 | `闭嘴` | `QUIET` | `CMD_QUIET` | `EVT_VOICE_COMMAND_QUIET` | `NONE` | `DO` | `ACT_QUIET` | 是 |
| `CAT-047` | 是 | 19 | `别叫` | `QUIET` | `CMD_QUIET` | `EVT_VOICE_COMMAND_QUIET` | `NONE` | `DO` | `ACT_QUIET` | 是 |
| `CAT-048` | 是 | 19 | `不许叫` | `QUIET` | `CMD_QUIET` | `EVT_VOICE_COMMAND_QUIET` | `NONE` | `DO` | `ACT_QUIET` | 是 |
| `CAT-049` | 否 | 20, 21, 22, 23, 24 | `小狗` | `CALL_NAME` | `CMD_CALL_NAME` | `EVT_VOICE_CALL_NAME` | `NONE` | `DO` | — | 是 |
| `CAT-050` | 否 | 20, 21, 22, 23, 24 | `小跟屁虫` | `CALL_NAME` | `CMD_CALL_NAME` | `EVT_VOICE_CALL_NAME` | `NONE` | `DO` | — | 是 |
| `CAT-051` | 否 | 20, 21, 22, 23, 24 | `小宝贝` | `CALL_NAME` | `CMD_CALL_NAME` | `EVT_VOICE_CALL_NAME` | `NONE` | `DO` | — | 是 |
| `CAT-052` | 否 | 20, 21, 22, 23, 24 | `乖狗狗` | `CALL_NAME` | `CMD_CALL_NAME` | `EVT_VOICE_CALL_NAME` | `NONE` | `DO` | — | 是 |
| `CAT-053` | 否 | 20, 21, 22, 23, 24 | `小坏蛋` | `CALL_NAME` | `CMD_CALL_NAME` | `EVT_VOICE_CALL_NAME` | `NONE` | `DO` | — | 是 |
| `CAT-054` | 否 | 25, 26, 27, 28, 29, 30, 31, 32, 33, 34 | `真棒` | `PRAISE` | `CMD_PRAISE` | `EVT_VOICE_PRAISE` | `PRAISE` | `NONE` | — | 否 |
| `CAT-055` | 否 | 25, 26, 27, 28, 29, 30, 31, 32, 33, 34 | `好狗` | `PRAISE` | `CMD_PRAISE` | `EVT_VOICE_PRAISE` | `PRAISE` | `NONE` | — | 否 |
| `CAT-056` | 否 | 25, 26, 27, 28, 29, 30, 31, 32, 33, 34 | `真乖` | `PRAISE` | `CMD_PRAISE` | `EVT_VOICE_PRAISE` | `PRAISE` | `NONE` | — | 否 |
| `CAT-057` | 否 | 25, 26, 27, 28, 29, 30, 31, 32, 33, 34 | `真聪明` | `PRAISE` | `CMD_PRAISE` | `EVT_VOICE_PRAISE` | `PRAISE` | `NONE` | — | 否 |
| `CAT-058` | 否 | 25, 26, 27, 28, 29, 30, 31, 32, 33, 34 | `可爱` | `PRAISE` | `CMD_PRAISE` | `EVT_VOICE_PRAISE` | `PRAISE` | `NONE` | — | 否 |
| `CAT-059` | 否 | 25, 26, 27, 28, 29, 30, 31, 32, 33, 34 | `厉害` | `PRAISE` | `CMD_PRAISE` | `EVT_VOICE_PRAISE` | `PRAISE` | `NONE` | — | 否 |
| `CAT-060` | 否 | 25, 26, 27, 28, 29, 30, 31, 32, 33, 34 | `太棒了` | `PRAISE` | `CMD_PRAISE` | `EVT_VOICE_PRAISE` | `PRAISE` | `NONE` | — | 否 |
| `CAT-061` | 否 | 25, 26, 27, 28, 29, 30, 31, 32, 33, 34 | `好样的` | `PRAISE` | `CMD_PRAISE` | `EVT_VOICE_PRAISE` | `PRAISE` | `NONE` | — | 否 |
| `CAT-062` | 否 | 25, 26, 27, 28, 29, 30, 31, 32, 33, 34 | `我爱你` | `PRAISE` | `CMD_PRAISE` | `EVT_VOICE_PRAISE` | `PRAISE` | `NONE` | — | 否 |
| `CAT-063` | 否 | 25, 26, 27, 28, 29, 30, 31, 32, 33, 34 | `我喜欢你` | `PRAISE` | `CMD_PRAISE` | `EVT_VOICE_PRAISE` | `PRAISE` | `NONE` | — | 否 |
| `CAT-064` | 否 | 25, 26, 27, 28, 29, 30, 31, 32, 33, 34 | `你真有趣` | `PRAISE` | `CMD_PRAISE` | `EVT_VOICE_PRAISE` | `PRAISE` | `NONE` | — | 否 |
| `CAT-065` | 否 | 25, 26, 27, 28, 29, 30, 31, 32, 33, 34 | `真有意思` | `PRAISE` | `CMD_PRAISE` | `EVT_VOICE_PRAISE` | `PRAISE` | `NONE` | — | 否 |
| `CAT-066` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `不可以咬` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-067` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `你怎么不听话` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-068` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `我要惩罚你咯` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-069` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `罚你站着` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-070` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `不准吃饭` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-071` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `321` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-072` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `我不跟你玩咯` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-073` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `我不理你咯` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-074` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `坏狗狗` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-075` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `笨狗` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-076` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `傻狗` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-077` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `我要生气咯` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-078` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `臭狗` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-079` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `我讨厌你` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `REPRIMAND` | `NONE` | — | 否 |
| `CAT-080` | 否 | 48 | `不怕不怕` | `COMFORT_DONT_BE_AFRAID` | `CMD_COMFORT_DONT_BE_AFRAID` | `EVT_VOICE_COMMAND_COMFORT_DONT_BE_AFRAID` | `NONE` | `DO` | `ACT_COMFORT_DONT_BE_AFRAID` | 是 |
| `CAT-081` | 否 | 49 | `疼不疼` | `ASK_IF_HURTS` | `CMD_ASK_IF_HURTS` | `EVT_VOICE_COMMAND_ASK_IF_HURTS` | `NONE` | `DO` | `ACT_ASK_IF_HURTS` | 是 |
| `CAT-082` | 否 | 50 | `没事没事` | `COMFORT_REASSURE` | `CMD_COMFORT_REASSURE` | `EVT_VOICE_COMMAND_COMFORT_REASSURE` | `NONE` | `DO` | `ACT_COMFORT_REASSURE` | 是 |
| `CAT-083` | 否 | 51 | `摸摸头` | `OFFER_HEAD_FOR_PET` | `CMD_OFFER_HEAD_FOR_PET` | `EVT_VOICE_COMMAND_OFFER_HEAD_FOR_PET` | `NONE` | `DO` | `ACT_OFFER_HEAD_FOR_PET` | 是 |
| `CAT-084` | 否 | 52 | `抱抱` | `SEEK_HUG` | `CMD_SEEK_HUG` | `EVT_VOICE_COMMAND_SEEK_HUG` | `NONE` | `DO` | `ACT_SEEK_HUG` | 是 |
| `CAT-085` | 否 | 53 | `吃饭` | `EAT_MEAL` | `CMD_EAT_MEAL` | `EVT_VOICE_COMMAND_EAT_MEAL` | `NONE` | `DO` | `ACT_EAT_MEAL` | 是 |
| `CAT-086` | 否 | 54 | `吃零食` | `EAT_SNACK` | `CMD_EAT_SNACK` | `EVT_VOICE_COMMAND_EAT_SNACK` | `NONE` | `DO` | `ACT_EAT_SNACK` | 是 |
| `CAT-087` | 否 | 55 | `吃罐罐` | `EAT_CANNED_FOOD` | `CMD_EAT_CANNED_FOOD` | `EVT_VOICE_COMMAND_EAT_CANNED_FOOD` | `NONE` | `DO` | `ACT_EAT_CANNED_FOOD` | 是 |
| `CAT-088` | 否 | 56 | `肚子饿不饿` | `RESPOND_HUNGRY_QUERY` | `CMD_RESPOND_HUNGRY_QUERY` | `EVT_VOICE_COMMAND_RESPOND_HUNGRY_QUERY` | `NONE` | `DO` | `ACT_RESPOND_HUNGRY_QUERY` | 是 |
| `CAT-089` | 否 | 57 | `想不想吃` | `RESPOND_WANT_EAT_QUERY` | `CMD_RESPOND_WANT_EAT_QUERY` | `EVT_VOICE_COMMAND_RESPOND_WANT_EAT_QUERY` | `NONE` | `DO` | `ACT_RESPOND_WANT_EAT_QUERY` | 是 |
| `CAT-090` | 否 | 58 | `你在吃啥` | `RESPOND_EATING_QUERY` | `CMD_RESPOND_EATING_QUERY` | `EVT_VOICE_COMMAND_RESPOND_EATING_QUERY` | `NONE` | `DO` | `ACT_RESPOND_EATING_QUERY` | 是 |
| `CAT-091` | 否 | 59 | `你想吃什么` | `RESPOND_FOOD_PREFERENCE_QUERY` | `CMD_RESPOND_FOOD_PREFERENCE_QUERY` | `EVT_VOICE_COMMAND_RESPOND_FOOD_PREFERENCE_QUERY` | `NONE` | `DO` | `ACT_RESPOND_FOOD_PREFERENCE_QUERY` | 是 |
| `CAT-092` | 否 | 60, 61 | `去尿尿` | `TOILET` | `CMD_TOILET` | `EVT_VOICE_COMMAND_TOILET` | `NONE` | `DO` | — | 是 |
| `CAT-093` | 否 | 60, 61 | `去便便` | `TOILET` | `CMD_TOILET` | `EVT_VOICE_COMMAND_TOILET` | `NONE` | `DO` | — | 是 |
| `CAT-094` | 否 | 62 | `擦一擦手` | `CLEAN` | `CMD_CLEAN` | `EVT_VOICE_COMMAND_CLEAN` | `NONE` | `DO` | — | 是 |
| `CAT-095` | 否 | 62 | `擦一擦脚` | `CLEAN` | `CMD_CLEAN` | `EVT_VOICE_COMMAND_CLEAN` | `NONE` | `DO` | — | 是 |
| `CAT-096` | 否 | 63, 64, 65, 66 | `睡觉` | `SLEEP` | `CMD_SLEEP` | `EVT_VOICE_COMMAND_SLEEP` | `NONE` | `DO` | — | 是 |
| `CAT-097` | 否 | 63, 64, 65, 66 | `睡吧` | `SLEEP` | `CMD_SLEEP` | `EVT_VOICE_COMMAND_SLEEP` | `NONE` | `DO` | — | 是 |
| `CAT-098` | 否 | 63, 64, 65, 66 | `去睡` | `SLEEP` | `CMD_SLEEP` | `EVT_VOICE_COMMAND_SLEEP` | `NONE` | `DO` | — | 是 |
| `CAT-099` | 否 | 63, 64, 65, 66 | `休息` | `SLEEP` | `CMD_SLEEP` | `EVT_VOICE_COMMAND_SLEEP` | `NONE` | `DO` | — | 是 |
| `CAT-100` | 否 | 67, 68, 69, 70, 71, 72, 73 | `来玩` | `PLAY` | `CMD_PLAY` | `EVT_VOICE_COMMAND_PLAY` | `NONE` | `DO` | — | 是 |
| `CAT-101` | 否 | 67, 68, 69, 70, 71, 72, 73 | `开始咯` | `PLAY` | `CMD_PLAY` | `EVT_VOICE_COMMAND_PLAY` | `NONE` | `DO` | — | 是 |
| `CAT-102` | 否 | 67, 68, 69, 70, 71, 72, 73 | `抓小狗游戏` | `PLAY` | `CMD_PLAY` | `EVT_VOICE_COMMAND_PLAY` | `NONE` | `DO` | — | 是 |
| `CAT-103` | 否 | 67, 68, 69, 70, 71, 72, 73 | `拔河比赛` | `PLAY` | `CMD_PLAY` | `EVT_VOICE_COMMAND_PLAY` | `NONE` | `DO` | — | 是 |
| `CAT-104` | 否 | 67, 68, 69, 70, 71, 72, 73 | `谁要玩游戏` | `PLAY` | `CMD_PLAY` | `EVT_VOICE_COMMAND_PLAY` | `NONE` | `DO` | — | 是 |
| `CAT-105` | 否 | 67, 68, 69, 70, 71, 72, 73 | `玩不玩` | `PLAY` | `CMD_PLAY` | `EVT_VOICE_COMMAND_PLAY` | `NONE` | `DO` | — | 是 |
| `CAT-106` | 否 | 67, 68, 69, 70, 71, 72, 73 | `一起玩` | `PLAY` | `CMD_PLAY` | `EVT_VOICE_COMMAND_PLAY` | `NONE` | `DO` | — | 是 |
| `CAT-107` | 否 | 74 | `我不要` | `REFUSE` | `CMD_REFUSE` | `EVT_VOICE_COMMAND_REFUSE` | `NONE` | `DO` | `ACT_REFUSE` | 是 |
| `CAT-108` | 否 | 75 | `我不玩` | `REFUSE_PLAY` | `CMD_REFUSE_PLAY` | `EVT_VOICE_COMMAND_REFUSE_PLAY` | `NONE` | `DO` | `ACT_REFUSE_PLAY` | 是 |
| `CAT-109` | 否 | 76 | `去找爸爸` | `FIND_DAD` | `CMD_FIND_DAD` | `EVT_VOICE_COMMAND_FIND_DAD` | `NONE` | `DO` | `ACT_FIND_DAD` | 是 |
| `CAT-110` | 否 | 77 | `去找妈妈` | `FIND_MOM` | `CMD_FIND_MOM` | `EVT_VOICE_COMMAND_FIND_MOM` | `NONE` | `DO` | `ACT_FIND_MOM` | 是 |
| `CAT-111` | 否 | 78 | `跳个舞` | `DANCE` | `CMD_DANCE` | `EVT_VOICE_COMMAND_DANCE` | `NONE` | `DO` | `ACT_DANCE` | 是 |
| `CAT-112` | 否 | 79 | `拿给我` | `BRING_TO_ME` | `CMD_BRING_TO_ME` | `EVT_VOICE_COMMAND_BRING_TO_ME` | `NONE` | `DO` | `ACT_BRING_TO_ME` | 是 |
| `CAT-113` | 否 | 80 | `给我` | `GIVE_TO_ME` | `CMD_GIVE_TO_ME` | `EVT_VOICE_COMMAND_GIVE_TO_ME` | `NONE` | `DO` | `ACT_GIVE_TO_ME` | 是 |
| `CAT-114` | 否 | 81 | `去拿` | `GO_GET_IT` | `CMD_GO_GET_IT` | `EVT_VOICE_COMMAND_GO_GET_IT` | `NONE` | `DO` | `ACT_GO_GET_IT` | 是 |
| `CAT-115` | 否 | 82 | `叼回来` | `BRING_IT_BACK` | `CMD_BRING_IT_BACK` | `EVT_VOICE_COMMAND_BRING_IT_BACK` | `NONE` | `DO` | `ACT_BRING_IT_BACK` | 是 |
| `CAT-116` | 否 | 83 | `捡球` | `FETCH_BALL` | `CMD_FETCH_BALL` | `EVT_VOICE_COMMAND_FETCH_BALL` | `NONE` | `DO` | `ACT_FETCH_BALL` | 是 |
| `CAT-117` | 否 | 84 | `找玩具` | `FIND_TOY` | `CMD_FIND_TOY` | `EVT_VOICE_COMMAND_FIND_TOY` | `NONE` | `DO` | `ACT_FIND_TOY` | 是 |
| `CAT-118` | 否 | 85 | `我要出门了` | `OWNER_GOING_OUT` | `CMD_OWNER_GOING_OUT` | `EVT_VOICE_COMMAND_OWNER_GOING_OUT` | `NONE` | `DO` | `ACT_OWNER_GOING_OUT` | 是 |
| `CAT-119` | 否 | 86 | `你自己在家` | `STAY_HOME_ALONE` | `CMD_STAY_HOME_ALONE` | `EVT_VOICE_COMMAND_STAY_HOME_ALONE` | `NONE` | `DO` | `ACT_STAY_HOME_ALONE` | 是 |
| `CAT-120` | 否 | 87 | `等我回来` | `WAIT_FOR_OWNER_RETURN` | `CMD_WAIT_FOR_OWNER_RETURN` | `EVT_VOICE_COMMAND_WAIT_FOR_OWNER_RETURN` | `NONE` | `DO` | `ACT_WAIT_FOR_OWNER_RETURN` | 是 |
| `CAT-121` | 否 | 88 | `拜拜` | `BYE_BYE` | `CMD_BYE_BYE` | `EVT_VOICE_COMMAND_BYE_BYE` | `NONE` | `DO` | `ACT_BYE_BYE` | 是 |
| `CAT-122` | 否 | 89 | `再见` | `GOODBYE` | `CMD_GOODBYE` | `EVT_VOICE_COMMAND_GOODBYE` | `NONE` | `DO` | `ACT_GOODBYE` | 是 |
| `CAT-123` | 否 | 90 | `我回来了` | `OWNER_RETURNED` | `CMD_OWNER_RETURNED` | `EVT_VOICE_COMMAND_OWNER_RETURNED` | `NONE` | `DO` | `ACT_OWNER_RETURNED` | 是 |
| `CAT-124` | 否 | 91 | `你在干嘛` | `ASK_WHAT_DOING` | `CMD_ASK_WHAT_DOING` | `EVT_VOICE_COMMAND_ASK_WHAT_DOING` | `NONE` | `DO` | `ACT_ASK_WHAT_DOING` | 是 |
| `CAT-125` | 否 | 92 | `你在哪里` | `ASK_WHERE_ARE_YOU` | `CMD_ASK_WHERE_ARE_YOU` | `EVT_VOICE_COMMAND_ASK_WHERE_ARE_YOU` | `NONE` | `DO` | `ACT_ASK_WHERE_ARE_YOU` | 是 |
| `CAT-126` | 否 | 93 | `你怎么了` | `ASK_WHATS_WRONG` | `CMD_ASK_WHATS_WRONG` | `EVT_VOICE_COMMAND_ASK_WHATS_WRONG` | `NONE` | `DO` | `ACT_ASK_WHATS_WRONG` | 是 |
| `CAT-127` | 否 | 94 | `你想什么` | `ASK_WHAT_THINKING` | `CMD_ASK_WHAT_THINKING` | `EVT_VOICE_COMMAND_ASK_WHAT_THINKING` | `NONE` | `DO` | `ACT_ASK_WHAT_THINKING` | 是 |
| `CAT-128` | 否 | 95 | `舒服吗` | `ASK_IF_COMFORTABLE` | `CMD_ASK_IF_COMFORTABLE` | `EVT_VOICE_COMMAND_ASK_IF_COMFORTABLE` | `NONE` | `DO` | `ACT_ASK_IF_COMFORTABLE` | 是 |
| `CAT-129` | 否 | 96 | `喜欢吗` | `ASK_IF_LIKES` | `CMD_ASK_IF_LIKES` | `EVT_VOICE_COMMAND_ASK_IF_LIKES` | `NONE` | `DO` | `ACT_ASK_IF_LIKES` | 是 |
| `CAT-130` | 否 | 97 | `好玩吗` | `ASK_IF_FUN` | `CMD_ASK_IF_FUN` | `EVT_VOICE_COMMAND_ASK_IF_FUN` | `NONE` | `DO` | `ACT_ASK_IF_FUN` | 是 |
| `CAT-131` | 否 | 98 | `你听得懂吗` | `ASK_IF_UNDERSTANDS` | `CMD_ASK_IF_UNDERSTANDS` | `EVT_VOICE_COMMAND_ASK_IF_UNDERSTANDS` | `NONE` | `DO` | `ACT_ASK_IF_UNDERSTANDS` | 是 |
| `CAT-132` | 否 | 99 | `你会什么` | `ASK_ABILITIES` | `CMD_ASK_ABILITIES` | `EVT_VOICE_COMMAND_ASK_ABILITIES` | `NONE` | `DO` | `ACT_ASK_ABILITIES` | 是 |
| `CAT-133` | 否 | 100 | `你学会了吗` | `ASK_IF_LEARNED` | `CMD_ASK_IF_LEARNED` | `EVT_VOICE_COMMAND_ASK_IF_LEARNED` | `NONE` | `DO` | `ACT_ASK_IF_LEARNED` | 是 |
| `CAT-134` | 否 | 101 | `我好想你` | `EXPRESS_MISS_YOU` | `CMD_EXPRESS_MISS_YOU` | `EVT_VOICE_COMMAND_EXPRESS_MISS_YOU` | `NONE` | `DO` | `ACT_EXPRESS_MISS_YOU` | 是 |
| `CAT-135` | 否 | 102 | `我累了` | `OWNER_TIRED` | `CMD_OWNER_TIRED` | `EVT_VOICE_COMMAND_OWNER_TIRED` | `NONE` | `DO` | `ACT_OWNER_TIRED` | 是 |
| `CAT-136` | 否 | 102 | `好累` | `OWNER_TIRED` | `CMD_OWNER_TIRED` | `EVT_VOICE_COMMAND_OWNER_TIRED` | `NONE` | `DO` | `ACT_OWNER_TIRED` | 是 |
| `CAT-137` | 否 | 103 | `我有点烦` | `OWNER_ANNOYED` | `CMD_OWNER_ANNOYED` | `EVT_VOICE_COMMAND_OWNER_ANNOYED` | `NONE` | `DO` | `ACT_OWNER_ANNOYED` | 是 |
| `CAT-138` | 否 | 103 | `烦死了` | `OWNER_ANNOYED` | `CMD_OWNER_ANNOYED` | `EVT_VOICE_COMMAND_OWNER_ANNOYED` | `NONE` | `DO` | `ACT_OWNER_ANNOYED` | 是 |
| `CAT-139` | 否 | 104 | `今天不开心` | `OWNER_UNHAPPY` | `CMD_OWNER_UNHAPPY` | `EVT_VOICE_COMMAND_OWNER_UNHAPPY` | `NONE` | `DO` | `ACT_OWNER_UNHAPPY` | 是 |
| `CAT-140` | 否 | 104 | `不得劲` | `OWNER_UNHAPPY` | `CMD_OWNER_UNHAPPY` | `EVT_VOICE_COMMAND_OWNER_UNHAPPY` | `NONE` | `DO` | `ACT_OWNER_UNHAPPY` | 是 |
| `CAT-141` | 否 | 105 | `过得不太顺利` | `OWNER_BAD_DAY` | `CMD_OWNER_BAD_DAY` | `EVT_VOICE_COMMAND_OWNER_BAD_DAY` | `NONE` | `DO` | `ACT_OWNER_BAD_DAY` | 是 |
| `CAT-142` | 否 | 106 | `我要抑郁了` | `OWNER_DEPRESSED` | `CMD_OWNER_DEPRESSED` | `EVT_VOICE_COMMAND_OWNER_DEPRESSED` | `NONE` | `DO` | `ACT_OWNER_DEPRESSED` | 是 |
| `CAT-143` | 否 | 106 | `心情不好` | `OWNER_DEPRESSED` | `CMD_OWNER_DEPRESSED` | `EVT_VOICE_COMMAND_OWNER_DEPRESSED` | `NONE` | `DO` | `ACT_OWNER_DEPRESSED` | 是 |
| `CAT-144` | 否 | 107 | `压力好大` | `OWNER_STRESSED` | `CMD_OWNER_STRESSED` | `EVT_VOICE_COMMAND_OWNER_STRESSED` | `NONE` | `DO` | `ACT_OWNER_STRESSED` | 是 |
| `CAT-145` | 否 | 108 | `我好孤独` | `OWNER_LONELY` | `CMD_OWNER_LONELY` | `EVT_VOICE_COMMAND_OWNER_LONELY` | `NONE` | `DO` | `ACT_OWNER_LONELY` | 是 |
| `CAT-146` | 否 | 109 | `我头疼` | `OWNER_UNWELL` | `CMD_OWNER_UNWELL` | `EVT_VOICE_COMMAND_OWNER_UNWELL` | `NONE` | `DO` | `ACT_OWNER_UNWELL` | 是 |
| `CAT-147` | 否 | 109 | `不舒服` | `OWNER_UNWELL` | `CMD_OWNER_UNWELL` | `EVT_VOICE_COMMAND_OWNER_UNWELL` | `NONE` | `DO` | `ACT_OWNER_UNWELL` | 是 |
| `CAT-148` | 否 | 110 | `我现在心情美美的` | `OWNER_HAPPY` | `CMD_OWNER_HAPPY` | `EVT_VOICE_COMMAND_OWNER_HAPPY` | `NONE` | `DO` | `ACT_OWNER_HAPPY` | 是 |
| `CAT-149` | 否 | 111 | `我今天很开心` | `OWNER_VERY_HAPPY` | `CMD_OWNER_VERY_HAPPY` | `EVT_VOICE_COMMAND_OWNER_VERY_HAPPY` | `NONE` | `DO` | `ACT_OWNER_VERY_HAPPY` | 是 |
| `CAT-150` | 否 | 111 | `很高兴` | `OWNER_VERY_HAPPY` | `CMD_OWNER_VERY_HAPPY` | `EVT_VOICE_COMMAND_OWNER_VERY_HAPPY` | `NONE` | `DO` | `ACT_OWNER_VERY_HAPPY` | 是 |
| `CAT-151` | 否 | 112 | `我感觉今天状态特别好` | `OWNER_FEELING_GREAT` | `CMD_OWNER_FEELING_GREAT` | `EVT_VOICE_COMMAND_OWNER_FEELING_GREAT` | `NONE` | `DO` | `ACT_OWNER_FEELING_GREAT` | 是 |
| `CAT-152` | 否 | 113 | `我今天感觉特别棒` | `OWNER_FEELING_EXCELLENT` | `CMD_OWNER_FEELING_EXCELLENT` | `EVT_VOICE_COMMAND_OWNER_FEELING_EXCELLENT` | `NONE` | `DO` | `ACT_OWNER_FEELING_EXCELLENT` | 是 |
| `CAT-153` | 否 | 114 | `我现在浑身都很轻松` | `OWNER_RELAXED` | `CMD_OWNER_RELAXED` | `EVT_VOICE_COMMAND_OWNER_RELAXED` | `NONE` | `DO` | `ACT_OWNER_RELAXED` | 是 |
| `CAT-154` | 否 | 115 | `今天真是个好日子` | `OWNER_WONDERFUL_DAY` | `CMD_OWNER_WONDERFUL_DAY` | `EVT_VOICE_COMMAND_OWNER_WONDERFUL_DAY` | `NONE` | `DO` | `ACT_OWNER_WONDERFUL_DAY` | 是 |
| `CAT-155` | 否 | 116 | `我今天太幸运了` | `OWNER_FEELING_LUCKY` | `CMD_OWNER_FEELING_LUCKY` | `EVT_VOICE_COMMAND_OWNER_FEELING_LUCKY` | `NONE` | `DO` | `ACT_OWNER_FEELING_LUCKY` | 是 |

## 4. 批量测试汇总

全量测试报告至少汇总：

- 路由覆盖：命中的不同 `command_key` 数 / `81`。
- 短语覆盖：已执行短语数 / `155`。
- 核心覆盖：已执行核心路由数 / `19`。
- 每条短语的计划次数、成功次数和准确率。
- Voice 发布结果与下游 Tree/Action 结果分开判定。

