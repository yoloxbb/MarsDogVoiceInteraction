# 确定性词库测试对齐表

本文档供测试人员逐条对齐 ASR 词库输入与最终语音事件。它是
[`config/command_catalog.yaml`](../config/command_catalog.yaml) 的可读测试快照；若两者不一致，
`command_catalog.yaml` 是唯一权威源。

## 1. 当前目录口径

- 目录版本：`2026-08-29-expanded-v1`。
- 源数据：`116` 条；事件路由：`81` 个；标准中文词/句：`155` 条。
- 每个标准词/句按配置生成 `10` 条受控扩展，共 `1550` 条扩展；包含标准词/句后，
  运行时精确匹配入口共 `1705` 条。
- 核心训练指令：`19` 组，对应表中“核心=是”的所有短语。
- 表中每条中文标准词/句都必须独立测试；同一 `command_key` 下的短语是等价入口。
- 英文 `reference_phrases_en` 当前只是参考元数据，不参与确定性词库匹配，因此不列入本表验收。
- `BT=是` 只表示 Voice 应设置 `should_trigger_behavior_tree=true`，不代表下游已经完成事件映射或动作执行。
- `action_name=—` 表示产品目录没有给出独立 `ACT_*`，但仍必须发布表中的 `event_type`。
- 所有“核心=是”的行都产生两条目录事件：先发不可执行
  `EVT_VOICE_COMMAND_KNOWN`（`dispatch_role=recognition_summary`），再发本表
  `event_type`（`dispatch_role=specific_command`）。KWS 只缓存候选，最终由
  `recognition_arbitration` 选择 KWS 或 ASR 目录作为唯一结果来源；核心命令被选中后
  才发布这两条事件。

## 2. 单条用例判定

每条用例至少满足：

1. `speech.asr_text` 与播放文本一致，或符合用例事先允许的等价转写；若符合下方
   “KWS 修正 ASR 同音转写”的条件，则 ASR 转写单项记为失败，但命令功能可按最终
   仲裁事件单独判定。
2. ASR 目录路径必须在同一个 `utterance_id` 出现
   `stage_complete stage=command_lexicon result=matched`；KWS 同音纠正路径则必须如实
   记录 `result=no_match`，并满足下方独立证据链。
3. 最终发布事件中的 `command_key/command_id/event_type` 与本表一致；核心行还须按
   下方三轴表检查 `social/intent/control/raw_nlu_tag`。
4. ASR 目录被选中时，最终 `event_publish.intent_source=command_lexicon`，且不再进入
   `stage=intent`；短句由 KWS 选中时来源为 `kws`。
5. 同一句必须有 `stage_complete stage=recognition_arbitration`，且只允许一个识别来源
   发布业务结果。核心命令的 KNOWN 摘要和具体事件属于同一个结果组。

### KWS 修正 ASR 同音转写的判定

核心短指令可能出现 ASR 同音误识别，但流式 KWS 候选正确。例如用户实际说“击掌”，
`speech.asr_text` 为“机长”时，只要同一个 `utterance_id` 同时满足以下条件，命令功能
仍判定为 **PASS（KWS 路径）**：

1. `stage=kws result=candidate`，候选为 `HIGH_FIVE` /
   `EVT_VOICE_COMMAND_HIGH_FIVE`；
2. `stage=command_lexicon result=no_match`，不得把“机长”伪报为目录命中；
3. `stage=recognition_arbitration result=kws_selected`、`selected_source=kws`，且
   `reason=short_asr_kws_preferred`；
4. 最终先发布一条不可执行的 `EVT_VOICE_COMMAND_KNOWN`，再发布一条可执行的
   `EVT_VOICE_COMMAND_HIGH_FIVE`，两条事件均属于 KWS 选中的同一结果组，不得重复，
   且该句不得再进入 Model K 产生另一组业务事件。

该结果只证明“击掌命令被正确路由”，不能把 `speech.asr_text=机长` 计为 ASR 正确，
也不能计为 `CAT-029` 的 `catalog_exact` 命中。测试报告应分别记录：ASR 转写单项
**FAIL（同音误识别）**、KWS/命令路由单项 **PASS**。若 ASR 已精确命中另一个目录命令、
同句存在多个不同 KWS 候选，或最终事件不是 `EVT_VOICE_COMMAND_HIGH_FIVE`，则不适用
此例外，必须按实际仲裁结果判定。

### 受控扩展的判定

扩展规则直接定义在 `command_catalog.yaml.expansion`，按语义分为 `command`、
`query`、`statement`、`social`、`vocative` 五种 profile。运行时启动时生成全部变体，
随后仍通过哈希表进行规范化整句精确匹配，不使用子串、编辑距离或否定词删除。

扩展命中时必须同时满足：

1. `command_key/event_type` 与对应标准词/句完全相同，并跳过 Model K。
2. `stage_complete(stage=command_lexicon)` 输出
   `match_strategy=rule_expansion/catalog_phrase/matched_phrase/expansion_profile/expansion_rule`。
3. KNOWN 摘要和具体事件的 `slots` 保留同样的规则取证字段；原词命中则为
   `match_strategy=catalog_exact`，且没有 `expansion_profile/expansion_rule`。
4. 自动覆盖测试必须验证 `155 × 10 = 1550` 条扩展全部可加载且无路由冲突；人工
   验收至少从每个 profile 抽取样本，并覆盖 19 组核心指令。
5. 未在规则中生成的句子继续走 Model K。例如“不要坐下”“请你不要坐下”不得命中
   `SIT`；“请你坐下”应按 `command/polite_please_you` 命中 `SIT`。

典型对齐样例：

| 输入 | 标准词/句 | profile / rule | 期望 `command_key` |
|---|---|---|---|
| 请你坐下 | 坐下 | `command/polite_please_you` | `SIT` |
| 宝贝，太棒了 | 太棒了 | `social/address_baby` | `PRAISE` |
| 我想问，你在哪里 | 你在哪里 | `query/ask_preface` | `ASK_WHERE_ARE_YOU` |
| 跟你说，我好孤独 | 我好孤独 | `statement/tell_you_preface` | `OWNER_LONELY` |
| 嘿，小狗 | 小狗 | `vocative/prefix_hey` | `CALL_NAME` |

说明：`PRAISE`、`SCOLD` 为非执行社交事件，`BT=否`。主表 `control` 是目录的具体
事件执行配置；核心事件 payload 的正式三轴以紧随其后的三轴表为准，因此 QUIET
虽然具体事件可执行，语义仍为 `NONE|BARK|STOP`。

### 19 组核心指令的三轴语义

| command_key | SOCIAL | INTENT | CONTROL |
|---|---|---|---|
| WALK | NONE | GO | DO |
| COME | NONE | COME | DO |
| FOLLOW | NONE | FOLLOW | DO |
| GO_OUT | NONE | GO_OUT | DO |
| GO_HOME | NONE | GO_HOME | DO |
| APPROACH | NONE | APPROACH | DO |
| BACK_UP | NONE | BACK | DO |
| SIT | NONE | SIT | DO |
| LIE_DOWN | NONE | LIE | DO |
| PLAY_DEAD | NONE | PLAY_DEAD | DO |
| STAND_UP | NONE | STAND | DO |
| STAND_STILL | NONE | STAY | DO |
| SHAKE_HAND | NONE | SHAKE | DO |
| HIGH_FIVE | NONE | HIGH_FIVE | DO |
| SPIN | NONE | SPIN | DO |
| ROLL_OVER | NONE | ROLL | DO |
| HOLD_POSITION | NONE | STAY | DO |
| DROP | NONE | DROP | DO |
| QUIET | NONE | BARK | STOP |

## 3. Model Intent 意图识别与事件对应表

本节用于测试与产品示例**语义相近、但没有命中确定性词库**的自然表达。测试前必须先
确认同一 `utterance_id` 的日志为：

```text
stage_complete stage=command_lexicon result=no_match
stage_complete stage=recognition_arbitration result=asr_selected
stage_complete stage=intent result=parsed
```

如果原句或受控扩展已命中目录，则应按第 4 节的具体目录事件判定，不能拿目录结果
冒充 Model K 准确率。Model K 的正式输出是 `SOCIAL|INTENT|CONTROL`；随后由开发侧
固定规则映射成下表事件。业务大类和 `EVT_VOICE_COMMAND_KNOWN` 摘要均为
`dispatch_role=semantic_classification`、`should_trigger_behavior_tree=false`。只有命中
第 3.2 节显式动作白名单时，才额外发布
`dispatch_role=specific_command`、`should_trigger_behavior_tree=true` 的具体动作事件。

### 3.1 三轴到事件的固定映射

| 触发条件 | 固定事件 | 说明 |
|---|---|---|
| `SOCIAL=CALL` | `EVT_VOICE_CALL_NAME` | 呼唤、昵称或吸引注意。 |
| `SOCIAL=PRAISE` | `EVT_VOICE_PRAISE` | 夸赞、鼓励。 |
| `SOCIAL=SCOLD` | `EVT_VOICE_SCOLD` | 责备、纠正。 |
| `SOCIAL=COMFORT` | `EVT_VOICE_COMFORT` | 安抚、关怀。 |
| `SOCIAL=PLAYFUL` | `EVT_VOICE_PLAY_INTERACTION` | 玩耍、逗弄语气。 |
| `SOCIAL=OWNER_POSITIVE` | `EVT_VOICE_POSITIVE_EMOTION` | 主人积极状态。 |
| `SOCIAL=OWNER_NEGATIVE` | `EVT_VOICE_NEGATIVE_EMOTION` | 主人消极状态。 |
| `INTENT!=NONE` 且 `CONTROL=QUERY` | `EVT_VOICE_STATUS_CARE` | 查询类统一进入状态关怀。 |
| `INTENT=PLAY/TUG/DANCE` 且非 `QUERY` | `EVT_VOICE_PLAY_INTERACTION` | 娱乐意图统一进入游戏互动。 |
| 其他 `INTENT!=NONE` 且 `CONTROL=DO/STOP` | `EVT_VOICE_COMMAND_KNOWN` | 只表示识别到粗类命令，不是具体动作事件。 |
| `NONE|NONE|NONE` | `EVT_VOICE_NEUTRAL` | 中性/OOS 统一事件；不可执行。 |

一个结果同时包含 SOCIAL、可执行白名单 INTENT 时，按“社交大类 → 具体动作 →
`EVT_VOICE_COMMAND_KNOWN` 摘要”的顺序发布；如果社交轴和意图轴导出同一个大类事件
则只发布一次。例如 `PLAYFUL|PLAY|DO` 只发布一个
`EVT_VOICE_PLAY_INTERACTION`，`SCOLD|EAT|STOP` 依次发布
`EVT_VOICE_SCOLD`、`EVT_VOICE_COMMAND_KNOWN`，而 `PRAISE|SIT|DO` 依次发布
`EVT_VOICE_PRAISE`、`EVT_VOICE_COMMAND_SIT`、`EVT_VOICE_COMMAND_KNOWN`。

### 3.2 Model Intent 具体动作白名单

只有下表一一对应、无需目标槽位的组合可以由模型额外生成具体动作事件。表外标签
仍只发布第 3.1 节业务大类；禁止通过字符串拼接自行生成事件名。

| `INTENT|CONTROL` | `command_key` | 具体事件 |
|---|---|---|
| `GO|DO` | `WALK` | `EVT_VOICE_COMMAND_WALK` |
| `COME|DO` | `COME` | `EVT_VOICE_COMMAND_COME` |
| `FOLLOW|DO` | `FOLLOW` | `EVT_VOICE_COMMAND_FOLLOW` |
| `GO_OUT|DO` | `GO_OUT` | `EVT_VOICE_COMMAND_GO_OUT` |
| `GO_HOME|DO` | `GO_HOME` | `EVT_VOICE_COMMAND_GO_HOME` |
| `APPROACH|DO` | `APPROACH` | `EVT_VOICE_COMMAND_APPROACH` |
| `BACK|DO` | `BACK_UP` | `EVT_VOICE_COMMAND_BACK_UP` |
| `SIT|DO` | `SIT` | `EVT_VOICE_COMMAND_SIT` |
| `LIE|DO` | `LIE_DOWN` | `EVT_VOICE_COMMAND_LIE_DOWN` |
| `PLAY_DEAD|DO` | `PLAY_DEAD` | `EVT_VOICE_COMMAND_PLAY_DEAD` |
| `SHAKE|DO` | `SHAKE_HAND` | `EVT_VOICE_COMMAND_SHAKE_HAND` |
| `HIGH_FIVE|DO` | `HIGH_FIVE` | `EVT_VOICE_COMMAND_HIGH_FIVE` |
| `SPIN|DO` | `SPIN` | `EVT_VOICE_COMMAND_SPIN` |
| `ROLL|DO` | `ROLL_OVER` | `EVT_VOICE_COMMAND_ROLL_OVER` |
| `DROP|DO` | `DROP` | `EVT_VOICE_COMMAND_DROP` |
| `BARK|STOP` | `QUIET` | `EVT_VOICE_COMMAND_QUIET` |
| `TOILET|DO` | `TOILET` | `EVT_VOICE_COMMAND_TOILET` |
| `CLEAN|DO` | `CLEAN` | `EVT_VOICE_COMMAND_CLEAN` |
| `SLEEP|DO` | `SLEEP` | `EVT_VOICE_COMMAND_SLEEP` |

`STAND/STAY/EAT/FIND_PERSON` 等标签存在动作分支或缺少目标槽位，当前不在白名单。
`FETCH/FIND_TOY` 使用下一节独立目标物门控。具体动作事件必须是事件组中唯一的可执行
事件；大类事件和 KNOWN 摘要不能再次生成行为树候选。

### 3.3 找物/捡取目标物门控

`FETCH/FIND_TOY` 不能只凭模型标签执行。节点必须从 ASR 原文进行确定性别名匹配，且
`object_name` 只能是 `config/object_targets.yaml` 中以下 18 个视觉类别之一：

```text
dog toy ball              dog frisbee toy          dog tug ring toy
dog collar                dog bowl                  dog leash
dog treat bag             dog food can              dog bed
trash can                 cardboard shipping box   sock
slipper                   tissue paper              door
stairs                    cat                       dog
```

匹配使用最长别名优先，中文和英文别名最终都转换为上面的规范英文类别。命中后：

| 模型标签 | 事件顺序 | 可执行事件 |
|---|---|---|
| `NONE|FIND_TOY|QUERY` | `EVT_VOICE_COMMAND_FETCH` → `EVT_VOICE_STATUS_CARE` | 只有 FETCH |
| `NONE|FIND_TOY|DO` | `EVT_VOICE_COMMAND_FETCH` → `EVT_VOICE_COMMAND_KNOWN` | 只有 FETCH |
| `NONE|FETCH|DO` | `EVT_VOICE_COMMAND_FETCH` → `EVT_VOICE_COMMAND_KNOWN` | 只有 FETCH |

具体事件使用 `command_key=FETCH/command_id=CMD_FETCH_OBJECT`，并携带：

```text
object_name=<18 类之一>
object_mention=<ASR 中命中的别名>
object_matched_alias=<目录别名>
object_match_source=asr_rule
object_catalog_version=<目录版本>
```

没有命中 18 类时，所有相关大类事件仍保留原三轴，但必须携带
`object_name=NONE/object_match_source=unsupported`，且不发布具体 FETCH 事件。例如
“看看那个布偶娃娃在哪里”只发布不可执行的 `EVT_VOICE_STATUS_CARE`，不能把整个三轴
改写成 `NONE|NONE|NONE`。

### 3.4 意图识别事件组示例表

下表集中给出 Model K 三轴标签经过开发侧固定路由后的最终事件。`BT` 一列与“发布
事件（按顺序）”逐项对应：`是` 表示该事件允许进入行为树，`否` 表示只传递语义或
状态。一个事件组内的所有事件必须共用同一 `interaction_id/utterance_id` 和
`raw_nlu_tag`。

| 输入示例 | 模型输出 `raw_nlu_tag` | 发布事件（按顺序） | `dispatch_role`（按顺序） | BT（按顺序） | 核对重点 |
|---|---|---|---|---|---|
| 旺财看看我 | `CALL|NONE|NONE` | `EVT_VOICE_CALL_NAME` | `semantic_classification` | 否 | 只表达呼唤，不生成动作候选。 |
| 你今天表现得特别优秀 | `PRAISE|NONE|NONE` | `EVT_VOICE_PRAISE` | `semantic_classification` | 否 | 单一社交事件。 |
| 你表现很好现在坐稳 | `PRAISE|SIT|DO` | `EVT_VOICE_PRAISE` → `EVT_VOICE_COMMAND_SIT` → `EVT_VOICE_COMMAND_KNOWN` | `semantic_classification` → `specific_command` → `semantic_classification` | 否 → 是 → 否 | 只有 SIT 是可执行事件；KNOWN 只是命令摘要。 |
| 往前走几步 | `NONE|GO|DO` | `EVT_VOICE_COMMAND_WALK` → `EVT_VOICE_COMMAND_KNOWN` | `specific_command` → `semantic_classification` | 是 → 否 | 模型标签使用 `GO`，具体事件按白名单映射为 WALK。 |
| 不允许再碰这些吃的 | `SCOLD|EAT|STOP` | `EVT_VOICE_SCOLD` → `EVT_VOICE_COMMAND_KNOWN` | `semantic_classification` → `semantic_classification` | 否 → 否 | `EAT|STOP` 不在具体动作白名单，不能拼出具体命令事件。 |
| 别紧张我就在这里 | `COMFORT|NONE|NONE` | `EVT_VOICE_COMFORT` | `semantic_classification` | 否 | 安抚大类事件。 |
| 咱们一起做个游戏 | `PLAYFUL|PLAY|DO` | `EVT_VOICE_PLAY_INTERACTION` | `semantic_classification` | 否 | SOCIAL 与 INTENT 导出同一事件时去重，只发布一次。 |
| 身体有没有哪里难受 | `NONE|DOG_STATUS|QUERY` | `EVT_VOICE_STATUS_CARE` | `semantic_classification` | 否 | 查询类统一路由到状态关怀。 |
| 看看那个球在哪里 | `NONE|FIND_TOY|QUERY` | `EVT_VOICE_COMMAND_FETCH` → `EVT_VOICE_STATUS_CARE` | `specific_command` → `semantic_classification` | 是 → 否 | ASR 目标匹配成功；FETCH 携带规范化 `object_name=dog toy ball`。 |
| 看看那个布偶娃娃在哪里 | `NONE|FIND_TOY|QUERY` | `EVT_VOICE_STATUS_CARE` | `semantic_classification` | 否 | 目标不在 18 类白名单；携带 `object_name=NONE/object_match_source=unsupported`。 |
| 最近事情太多让我很焦虑 | `OWNER_NEGATIVE|NONE|NONE` | `EVT_VOICE_NEGATIVE_EMOTION` | `semantic_classification` | 否 | 主人消极状态大类。 |
| 今天所有事情都特别顺心 | `OWNER_POSITIVE|NONE|NONE` | `EVT_VOICE_POSITIVE_EMOTION` | `semantic_classification` | 否 | 主人积极状态大类。 |
| 等一下记得读这条消息 | `NONE|NONE|NONE` | `EVT_VOICE_NEUTRAL` | `semantic_classification` | 否 | 合法中性/OOS 标签，不等同于模型解析失败。 |
| `<模型输出不符合三轴协议>` | 无有效标签 | `EVT_VOICE_COMMAND_UNKNOWN` | `diagnostic` | 否 | 仅模型和规则均无有效协议结果时使用，不能代替合法 NEUTRAL。 |

测试时不能只检查事件组中“出现过某个事件”。必须同时核对事件数量、发布顺序和每条
事件的 `should_trigger_behavior_tree`；尤其禁止让 `EVT_VOICE_COMMAND_KNOWN` 与具体
动作事件同时进入行为树，避免同一句生成两个动作候选。

### 3.5 产品示例的相似句测试表

下列“测试相似句”均已确认不在当前 1705 个确定性匹配入口中，适合直接验证 Model K。
测试团队还应围绕每行自行补充同义改写，但期望标签必须遵守训练标注协议，不能仅凭
最终事件反推模型标签。日志必须同时核对 `raw_nlu_tag` 和按序发布的 `event_types`。

| 产品类别 / 原示例 | 测试相似句示例 | 期望 `raw_nlu_tag` | 期望事件（按顺序） |
|---|---|---|---|
| 走 / 去 | 往前走几步 | `NONE|GO|DO` | `EVT_VOICE_COMMAND_WALK` → `EVT_VOICE_COMMAND_KNOWN` |
| 过来 / 回来 / 到我这儿来 | 赶紧回到我身边 | `NONE|COME|DO` | `EVT_VOICE_COMMAND_COME` → `EVT_VOICE_COMMAND_KNOWN` |
| 跟着我 / 跟我走 | 一路跟在我后面 | `NONE|FOLLOW|DO` | `EVT_VOICE_COMMAND_FOLLOW` → `EVT_VOICE_COMMAND_KNOWN` |
| 出去玩 / 出去溜溜 | 咱们到外面溜达 | `NONE|GO_OUT|DO` | `EVT_VOICE_COMMAND_GO_OUT` → `EVT_VOICE_COMMAND_KNOWN` |
| 回家 | 现在回到你的窝里 | `NONE|GO_HOME|DO` | `EVT_VOICE_COMMAND_GO_HOME` → `EVT_VOICE_COMMAND_KNOWN` |
| 靠近点 | 再贴近我一些 | `NONE|APPROACH|DO` | `EVT_VOICE_COMMAND_APPROACH` → `EVT_VOICE_COMMAND_KNOWN` |
| 退后 | 向后退两步 | `NONE|BACK|DO` | `EVT_VOICE_COMMAND_BACK_UP` → `EVT_VOICE_COMMAND_KNOWN` |
| 坐 / 坐下 / 蹲下 | 把屁股坐稳 | `NONE|SIT|DO` | `EVT_VOICE_COMMAND_SIT` → `EVT_VOICE_COMMAND_KNOWN` |
| 趴下 / 躺下 | 趴到垫子上 | `NONE|LIE|DO` | `EVT_VOICE_COMMAND_LIE_DOWN` → `EVT_VOICE_COMMAND_KNOWN` |
| 装死 | 假装中枪倒下 | `NONE|PLAY_DEAD|DO` | `EVT_VOICE_COMMAND_PLAY_DEAD` → `EVT_VOICE_COMMAND_KNOWN` |
| 起来 / 站起来 | 站端正了 | `NONE|STAND|DO` | `EVT_VOICE_COMMAND_KNOWN` |
| 站好 / 站着 | 保持站立姿势 | `NONE|STAY|DO` | `EVT_VOICE_COMMAND_KNOWN` |
| 别动 / 等着 / 不许动 | 保持原地不要走 | `NONE|STAY|DO` | `EVT_VOICE_COMMAND_KNOWN` |
| 握手 / 抬手 | 把爪子递给我 | `NONE|SHAKE|DO` | `EVT_VOICE_COMMAND_SHAKE_HAND` → `EVT_VOICE_COMMAND_KNOWN` |
| 击掌 / 拍手 | 抬起爪子和我碰掌 | `NONE|HIGH_FIVE|DO` | `EVT_VOICE_COMMAND_HIGH_FIVE` → `EVT_VOICE_COMMAND_KNOWN` |
| 转圈 | 原地绕一整圈 | `NONE|SPIN|DO` | `EVT_VOICE_COMMAND_SPIN` → `EVT_VOICE_COMMAND_KNOWN` |
| 翻滚 | 在地上滚一圈 | `NONE|ROLL|DO` | `EVT_VOICE_COMMAND_ROLL_OVER` → `EVT_VOICE_COMMAND_KNOWN` |
| 放下 / 松开 / 松口 | 把嘴里的东西松开 | `NONE|DROP|DO` | `EVT_VOICE_COMMAND_DROP` → `EVT_VOICE_COMMAND_KNOWN` |
| 安静 / 闭嘴 / 别叫 | 现在不要发出叫声 | `NONE|BARK|STOP` | `EVT_VOICE_COMMAND_QUIET` → `EVT_VOICE_COMMAND_KNOWN` |
| 昵称：小狗 / 小宝贝等 | 旺财看看我 | `CALL|NONE|NONE` | `EVT_VOICE_CALL_NAME` |
| 夸奖 / 鼓励 | 你今天表现得特别优秀 | `PRAISE|NONE|NONE` | `EVT_VOICE_PRAISE` |
| 夸奖并要求坐下 | 你表现很好现在坐稳 | `PRAISE|SIT|DO` | `EVT_VOICE_PRAISE` → `EVT_VOICE_COMMAND_SIT` → `EVT_VOICE_COMMAND_KNOWN` |
| 一般责备 / 纠正 | 你这样做真的不听话 | `SCOLD|NONE|NONE` | `EVT_VOICE_SCOLD` |
| 不准吃饭 | 不允许再碰这些吃的 | `SCOLD|EAT|STOP` | `EVT_VOICE_SCOLD` → `EVT_VOICE_COMMAND_KNOWN` |
| 不怕不怕 / 没事没事 | 别紧张我就在这里 | `COMFORT|NONE|NONE` | `EVT_VOICE_COMFORT` |
| 疼不疼 / 你怎么了 | 身体有没有哪里难受 | `NONE|DOG_STATUS|QUERY` | `EVT_VOICE_STATUS_CARE` |
| 摸摸头 / 抱抱 | 让我抱抱安慰你 | `COMFORT|NONE|NONE` | `EVT_VOICE_COMFORT` |
| 吃饭 / 吃零食 / 吃罐罐 | 现在去吃点东西 | `NONE|EAT|DO` | `EVT_VOICE_COMMAND_KNOWN` |
| 肚子饿不饿 / 想不想吃 / 吃啥 | 现在是不是有点饿 | `NONE|EAT|QUERY` | `EVT_VOICE_STATUS_CARE` |
| 去尿尿 / 去便便 | 该去解决一下大小便了 | `NONE|TOILET|DO` | `EVT_VOICE_COMMAND_TOILET` → `EVT_VOICE_COMMAND_KNOWN` |
| 擦一擦手 / 脚 | 把爪子清理干净 | `NONE|CLEAN|DO` | `EVT_VOICE_COMMAND_CLEAN` → `EVT_VOICE_COMMAND_KNOWN` |
| 睡觉 / 睡吧 / 休息 | 回窝好好休息一会儿 | `NONE|SLEEP|DO` | `EVT_VOICE_COMMAND_SLEEP` → `EVT_VOICE_COMMAND_KNOWN` |
| 来玩 / 一起玩 / 玩不玩 | 咱们一起做个游戏 | `PLAYFUL|PLAY|DO` | `EVT_VOICE_PLAY_INTERACTION`（去重后 1 条） |
| 拔河比赛 | 和我进行一场拔河 | `PLAYFUL|TUG|DO` | `EVT_VOICE_PLAY_INTERACTION`（去重后 1 条） |
| 我不要 / 我不玩 | 这个游戏先不要继续了 | `NONE|PLAY|STOP` | `EVT_VOICE_PLAY_INTERACTION` |
| 去找爸爸 / 去找妈妈 | 帮我找到家里的爸爸 | `NONE|FIND_PERSON|DO` | `EVT_VOICE_COMMAND_KNOWN` |
| 跳个舞 | 给大家表演一段舞蹈 | `NONE|DANCE|DO` | `EVT_VOICE_PLAY_INTERACTION` |
| 拿给我 / 给我 / 去拿 / 叼回来 / 捡球 | 把那只球叼到我面前 | `NONE|FETCH|DO` | `EVT_VOICE_COMMAND_FETCH` → `EVT_VOICE_COMMAND_KNOWN` |
| 查询受支持目标 | 看看那个球在哪里 | `NONE|FIND_TOY|QUERY` | `EVT_VOICE_COMMAND_FETCH` → `EVT_VOICE_STATUS_CARE` |
| 查询不支持目标 | 看看那个布偶娃娃在哪里 | `NONE|FIND_TOY|QUERY` | `EVT_VOICE_STATUS_CARE`；`object_name=NONE` |
| 找泛指玩具 | 看看玩具被放在哪里 | `NONE|FIND_TOY|DO` | `EVT_VOICE_COMMAND_KNOWN`；`object_name=NONE` |
| 我要出门了 / 你自己在家 / 等我回来 / 拜拜 | 我现在要去上班了 | `NONE|OWNER_LEAVE|DO` | `EVT_VOICE_COMMAND_KNOWN` |
| 我回来了 | 我已经到家啦 | `NONE|OWNER_RETURN|DO` | `EVT_VOICE_COMMAND_KNOWN` |
| 你在干嘛 / 你在哪里 / 你想什么 | 告诉我你现在在做什么 | `NONE|DOG_STATUS|QUERY` | `EVT_VOICE_STATUS_CARE` |
| 喜欢吗 / 好玩吗 | 这个游戏你觉得有意思吗 | `NONE|DOG_PREFERENCE|QUERY` | `EVT_VOICE_STATUS_CARE` |
| 你听得懂吗 / 你会什么 / 你学会了吗 | 你现在能够完成哪些动作 | `NONE|DOG_CAPABILITY|QUERY` | `EVT_VOICE_STATUS_CARE` |
| 我好想你 | 一整天没见到你我很想念 | `OWNER_NEGATIVE|NONE|NONE` | `EVT_VOICE_NEGATIVE_EMOTION` |
| 主人消极状态表达 | 最近事情太多让我很焦虑 | `OWNER_NEGATIVE|NONE|NONE` | `EVT_VOICE_NEGATIVE_EMOTION` |
| 主人积极状态表达 | 今天所有事情都特别顺心 | `OWNER_POSITIVE|NONE|NONE` | `EVT_VOICE_POSITIVE_EMOTION` |

产品文档中的“饥饿值、排泄值、清洁值、困倦值”等执行条件属于行为树/Action 的下游
前置条件，不是 Voice 的分类标签。Voice 只授权白名单具体事件进入行为树；是否满足
数值门限、是否真正执行仍必须另做下游验收。

### 3.6 结果判定要求

每条 Model K 用例必须同时满足：

1. `stage=intent result=parsed`，且 `social/intent/control` 与期望标签逐轴一致；
2. `event_publish.raw_nlu_tag` 与期望完整三轴一致，不能只检查某一个轴；
3. `event_types` 的数量和顺序与表格一致，同一大类事件必须去重；
4. 所有模型事件均为 `intent_source=rkllm_model_k`（模型不可用并明确测试规则回退时才
   允许 `rule_model_k_compatible`）；只有白名单具体事件允许
   `should_trigger_behavior_tree=true`，业务大类和 KNOWN 摘要必须为 `false`；
5. 合法 `NONE|NONE|NONE` 应发布一条不可执行的 `EVT_VOICE_NEUTRAL`；只有模型和规则均
   没有有效协议结果时才允许非执行诊断事件 `EVT_VOICE_COMMAND_UNKNOWN`。
6. `FETCH/FIND_TOY` 还必须出现 `stage=object_target`。只有 `result=matched` 且
   `object_name` 属于 18 类白名单时允许发布可执行 FETCH；`unsupported/unavailable`
   均不得发布具体事件。

## 4. 词/句与事件逐条对齐表

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
| `CAT-066` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `不可以咬` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-067` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `你怎么不听话` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-068` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `我要惩罚你咯` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-069` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `罚你站着` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-070` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `不准吃饭` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-071` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `321` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-072` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `我不跟你玩咯` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-073` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `我不理你咯` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-074` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `坏狗狗` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-075` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `笨狗` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-076` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `傻狗` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-077` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `我要生气咯` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-078` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `臭狗` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
| `CAT-079` | 否 | 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47 | `我讨厌你` | `SCOLD` | `CMD_SCOLD` | `EVT_VOICE_SCOLD` | `SCOLD` | `NONE` | — | 否 |
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

## 5. 批量测试汇总

全量测试报告至少汇总：

- 路由覆盖：命中的不同 `command_key` 数 / `81`。
- 短语覆盖：已执行短语数 / `155`。
- 核心覆盖：已执行核心路由数 / `19`。
- 每条短语的计划次数、成功次数和准确率。
- Voice 发布结果与下游 Tree/Action 结果分开判定。
