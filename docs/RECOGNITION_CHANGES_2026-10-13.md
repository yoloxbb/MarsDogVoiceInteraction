# 语音识别内容改动说明

日期：2026 年 10 月 13 日。项目：MarsDogVoiceInteraction。
词库版本：`2026-10-13-recognition-v9`。

本次根据产品识别表补齐同义词、调整命令归属，并增加带物品和地点参数的整句识别。
例如，“走”现在发布跟随事件，“把球拿来”携带目标物名称，“到客厅去”携带地点标识。
为避免长句被零散关键词提前截走，正式配置改为整句确认后发布。

交付范围是**听觉识别和事件发布**。调用方负责地图点位绑定、导航、视觉搜索、
取物和其他动作执行。语音事件发布成功不表示机器人已经完成动作。

下游开发请使用 [开发对接文档](DOWNSTREAM_INTEGRATION.md)和
[完整命令映射表 CSV](DOWNSTREAM_COMMAND_MAP.csv)，按接口、处理标志和验收用例接入。

**固定词句的语义调整**

| 识别内容 | 修改前 | 修改后 |
|---|---|---|
| 走 | `WALK`，随机走开 | `FOLLOW`，跟随 |
| 站好、站着 | `STAND_STILL`，保持站立 | `STAND_UP`，起身站立 |
| 不可以咬 | `SCOLD`，责备 | `DROP`，松口 |
| 小跟屁虫、小宝贝、乖狗狗 | `CALL_NAME`，呼名 | `PRAISE`，夸赞 |
| 小宝贝儿 | 呼名变体 | 夸赞变体 |

原有“去、走开、往前走”仍属于 `WALK`；保持站立功能通过“保持站立、站着别动、
站住”保留。“小狗、跟屁虫”等呼名与硬件唤醒分开，不会因为加入词库就成为待机唤醒词。

KWS 原始词表及生成词表同步调整了“走、站好、站着”的事件归属，避免关键词路径
与整句词库路径产生不同含义。

**补充的口语表达**

| 类别 | 新增或调整的说法 |
|---|---|
| 跟随 | 跟上 |
| 靠近 | 过来点、再过来些 |
| 握手 | 握手手、手手、握爪、握爪子 |
| 转圈 | 转一转 |
| 翻滚 | 翻一圈 |
| 松口 | 不可以吃 |
| 保持姿态 | 不可以、No、定 |
| 呼名 | 跟屁虫 |
| 夸赞 | 棒棒的、太厉害了、太厉害、好厉害、你真厉害、厉害了、真厉害、好厉害啊、你太厉害了，以及上表迁入的昵称表达 |
| 责备 | 不跟你玩了、不理你、不想理你、讨厌你、臭宝、我要生气了、我生气了、我要惩罚你、罚你不准吃饭、我不理你了 |

词库仍采用整句精确匹配、受控扩展和已有的拼音同音兜底，不把任意包含动作词的长句
直接视为动作命令。人工登记的口语变体不再自动生成十种扩展。

| 固定词库统计 | 修改前 | 修改后 |
|---|---:|---:|
| 路由组 | 83 | 83 |
| 核心指令组 | 19 | 19 |
| 标准词句 | 158 | 159 |
| 受控扩展 | 1580 | 1590 |
| 人工登记变体 | 70 | 103 |
| 精确匹配入口总数 | 1808 | 1852 |

上述统计不包含下面的参数句式组合，也不代表真人语音识别准确率。`source_rows`
继续保留历史产品目录的 116 条源数据分组编号。

**新增整句取物识别**

固定词库未命中时，新增解析器识别“去拿某物”“把某物拿来”“找找某物”等完整句式，
支持已配置的称呼、礼貌前缀和部分语气词，不依赖意图大模型。

| 输入示例 | 发布事件 | 主要参数 |
|---|---|---|
| 去拿球、把球拿来 | `EVT_VOICE_COMMAND_FETCH` | `object_name=dog toy ball`、`object_mention=球` |
| 帮我把飞盘拿过来 | `EVT_VOICE_COMMAND_FETCH` | `object_name=dog frisbee toy`、`object_mention=飞盘` |
| 找找那个纸箱 | `EVT_VOICE_COMMAND_FETCH` | `object_name=cardboard shipping box`、`object_mention=纸箱` |

命令编号复用 `CMD_FETCH_OBJECT`，目标类别复用现有 18 类目标物词表。
提取出来的整个名词必须匹配已知别名：“篮球鞋”不能因为包含“球”就被当成球，
“球和飞盘”也不会被截成单一目标。已有固定命令如“捡球、找玩具”优先走原词库路由。

“去拿玩具”“把咬绳拿来”等句式可以提取物品名称，但“玩具”“咬绳”目前没有
对应的规范目标类别，因此发布识别摘要：`object_name=NONE`，保留 `object_mention`，
并设置 `reason=unsupported_object`、`is_executable=false`、
`should_trigger_behavior_tree=false`。调用方可读取原始名称，但不能把该摘要当成
已经确认目标的执行命令。

**新增地点识别与发布契约**

新增 `EVT_VOICE_COMMAND_GO_TO_LOCATION`，命令编号为 `CMD_GO_TO_LOCATION`。
“去某地、到某地去、回某地去”使用相同的地点事件，不冒用原有 `GO_HOME`。

| 地点名称 | 可识别别名 | 发布的 location_id |
|---|---|---|
| 狗窝 | 狗窝、狗床 | `dog_bed` |
| 房间 | 房间 | `room` |
| 卧室 | 卧室、睡房 | `bedroom` |
| 客厅 | 客厅 | `living_room` |
| 厨房 | 厨房 | `kitchen` |

地点词表保存在 [location_targets.yaml](../config/location_targets.yaml)。这些 ID 是
听觉层的语义标识，不是已经绑定的地图点位。**调用方自行把地点标识转换成实际点位，
Voice 不提供地图坐标。**

“到客厅去”的关键发布字段如下；这是字段摘录，运行时还包含会话、语句、声纹和时间等
AudioEvent v2 公共字段。`slots` 的实际格式是键值列表。

```json
{
  "event_type": "EVT_VOICE_COMMAND_GO_TO_LOCATION",
  "command_id": "CMD_GO_TO_LOCATION",
  "asr_text": "到客厅去",
  "intent_source": "parameterized_command",
  "dispatch_role": "specific_command",
  "is_executable": true,
  "should_trigger_behavior_tree": true,
  "slots": [
    {"key": "location_id", "value": "living_room"},
    {"key": "location_name", "value": "客厅"},
    {"key": "location_mention", "value": "客厅"},
    {"key": "location_catalog_version", "value": "2026-10-13-v1"},
    {"key": "command_key", "value": "GO_TO_LOCATION"},
    {"key": "match_strategy", "value": "sentence_template"}
  ]
}
```

未知地点如“去办公室”仍发布地点识别事件，但 `location_id=NONE`，
`location_name/location_mention=办公室`，`reason=unsupported_location`，
`dispatch_role=recognition_summary`，两个执行标志均为 `false`。

取物和地点参数事件使用 `intent_source=parameterized_command`，不附带额外的
`EVT_VOICE_COMMAND_KNOWN` 摘要。调用方不能仅依据事件名判断是否执行，必须检查
执行标志和目标参数。夸赞、责备等原有社交反应仍允许
`is_executable=false`、`should_trigger_behavior_tree=true`，与未知目标摘要不同。

**发布时机与否定处理**

正式链路先缓存 KWS 候选，再等待整句 ASR。固定词库命中时使用目录语义；
未命中时尝试取物和地点句式；仍未匹配则发布 UNKNOWN。正式配置的规则意图与
大模型意图均关闭，新句式可以独立工作。

参数句式优先于零散关键词候选，避免“把球拿来”只触发“来”、“罚你站着”只触发
站立。既有的 KWS 命令与 ASR 精确错写白名单例外继续保留。

| 配置项 | 修改前 | 修改后 |
|---|---|---|
| `providers.kws.config.publish_mode` | `immediate` | `deferred` |
| `providers.kws.config.kws_fallback_on_asr_empty` | `true` | `false` |
| `interaction.idle_timeout_sec` | 20 秒 | 30 秒 |
| `parameterized_commands` | 无 | 启用，并加载地点词表 |

整句确认增加了等待说话结束及 ASR 处理的时间，本次没有给出现场响应延迟指标。
即时模式仍可选，但切回即时模式后，关键词抢先发布、长句参数无法补入的限制仍然存在。

声学否定检测扩展到两种发布模式。检测到“不要、别、不准、不许”后阻止本句动作回退，
即使 ASR 漏掉否定前缀也不会直接放行。原有“别动、不准动、不许动、别叫、不许叫”
整句精确匹配例外继续保留；新增精确匹配 SCOLD 整句的社交反应例外，例如
“罚你不准吃饭”，不会执行其中的动作片段。同音匹配不能绕过该否定拦截。

**实现文件与配套文档**

| 文件 | 变更职责 |
|---|---|
| [command_catalog.yaml](../config/command_catalog.yaml) | 固定词句、口语变体和事件归属 |
| [kws_keywords_raw.txt](../config/kws_keywords_raw.txt)、[kws_keywords.txt](../config/kws_keywords.txt) | 同步关键词事件归属 |
| [parameterized_commands.py](../marsdog_voice_interaction/core/parameterized_commands.py) | 新增整句取物与地点解析器 |
| [object_target_resolver.py](../marsdog_voice_interaction/core/object_target_resolver.py) | 新增完整名词精确匹配 |
| [location_targets.yaml](../config/location_targets.yaml) | 新增地点标识与别名词表 |
| [voice_interaction_node.py](../marsdog_voice_interaction/nodes/voice_interaction_node.py) | 接入后台识别、参数事件发布和否定例外 |
| [voice_event_types.py](../marsdog_voice_interaction/messages/voice_event_types.py) | 声明新增地点事件 |
| [kws_sherpa.py](../marsdog_voice_interaction/providers/kws_sherpa.py) | 在 deferred 模式保留声学否定检测 |
| [config_loader.py](../marsdog_voice_interaction/utils/config_loader.py) | 解析地点词表相对路径 |
| [voice.yaml](../config/voice.yaml)、[voice.pipeline.mock.yaml](../config/voice.pipeline.mock.yaml) | 正式配置与 Mock 接入 |
| [test_product_recognition.py](../tests/test_product_recognition.py) | 新增产品词句、参数事件、后台识别和冲突回归测试 |

已有词库、事件协议、KWS、否定处理和配置测试同步更新。
[README](../README.md)、[接口契约](ROS2_CONTRACT.md)、[交接说明](HANDOFF.md)、
[词库测试矩阵](COMMAND_CATALOG_TEST_MATRIX.md)和[测试日志指南](TESTING_LOG_GUIDE.md)
已同步本次变化。现有打包规则已涵盖配置 YAML，Python 模块由包发现及安装规则处理，
本次无需修改 `MANIFEST.in`。

18:36 现场反馈补充：ASR 已转写出“太厉害了”，但此前缺少这条完整口语变体，
导致词库未命中并发布 UNKNOWN。本次补齐八种“厉害”相关说法；仍不对包含“厉害”的
任意长句做子串触发。

19:04 现场反馈补充：“我生气了”已被 ASR 正确转写，但缺少完整词句而返回 UNKNOWN。
新增该句作为现有 SCOLD 的口语变体，发布 `CMD_SCOLD` / `EVT_VOICE_COMMAND_SCOLD`，
`dispatch_role=social_reaction`、`is_executable=false`、`should_trigger_behavior_tree=true`。
词库版本更新为 `2026-10-13-recognition-v9`；“我你我生气了”等异常文本、否定句及
包含该片段的问句不作为同一责备表达放行。词库在启动时加载，更新后须重启节点生效。

**验证结果与交付状态**

实现阶段的软件验证结果如下，多轮测试存在重叠，不能相加作为唯一用例数。

| 验证轮次 | 结果 | 覆盖说明 |
|---|---|---|
| 全量测试 | 461 通过、8 跳过 | 8 项需要指定的官方 KWS 模型配套录音或现场录音 |
| 最终相关回归 | 84 通过 | 产品识别与否定处理，包含随后补充的 3 个后台识别用例 |
| 18:36 现场反馈补词回归 | 164 通过 | 产品识别与词库测试；补测八种夸赞表达、无关 KWS 候选以及否定和长句不误触发 |
| 19:04 现场反馈补词回归 | 171 通过 | 产品识别与词库测试；新增“我生气了”发布验证及异常转写、否定、第三人称和问句不误触发验证 |
| KWS 词表校验 | 通过 | 动作与否定词表均与生成结果一致，音素均存在于模型 tokens 中 |

测试使用当前源码与已有测试环境；项目自身 `.venv` 在验证时未安装 pytest。
软件结果不代表麦克风实测准确率、现场误触发率、声纹准确率或机器人实际执行效果。

当前交付为工作区源码与文档修改，尚未完成本次改动的 colcon 重建、节点重启和真机验收。
调用方接入时需要完成以下事项：

1. 接收新增地点事件及 `location_id/location_name/location_mention`，自行实现点位绑定。
2. 复用取物事件时读取规范 `object_name`，对未知目标摘要按执行标志处理。
3. 更新“走、站好、站着、不可以咬”和相关昵称的测试预期。
4. 在部署环境重建并重新加载正确工作空间，重启后验收实际语音、事件内容及整句响应延迟。

硬件多唤醒词仍由唤醒模块配置；靠近距离、随机动作组合、取物失败等待和行为优先级
由调用方实现，不在本次听觉改动的完成范围内。
