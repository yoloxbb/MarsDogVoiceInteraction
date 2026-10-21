# 确定性词库测试对齐表

由 `scripts/generate_command_catalog.py` 生成。修改 XLSX 和审核规则后重新生成。

版本 `2026-10-19-workbook-v15`，来源 [docs/语音识别内容.xlsx](语音识别内容.xlsx) 的 Sheet1。
共 49 个功能条目：43 个固定路由（含 ASR 呼名），4 个参数句式、1个闲聊兜底和1个声源入口。
标准词句 222 条，受控整句扩展 2220 条，复用口语/ASR变体 123 条，共 2565 个精确入口。
`source_rows` 是 Excel 实际行号。叫两声/说话优先 BARK；去/走开/翻滚等旧独立路由已移出本版。

每句话只发布一个具体事件；PRAISE/SCOLD 为 social_reaction（可执行=false，BT=true）；CALL_NAME 两项均 false；其余固定路由两项均 true。
BT=true 表示 Voice 允许下游接收，不证明 Tree/Action 已实现或真机已执行。硬件唤醒仍由模块配置，ASR 呼名不代替唤醒。

| Excel行 | 命令 | 标准词句 | 事件 / 编号 |
|---|---|---|---|
| 3 | CALL_NAME | 小火小火、小狗、跟屁虫、嘬嘬嘬 | `EVT_VOICE_CALL` / `CMD_CALL_NAME` |
| 4 | QUIET | 安静、闭嘴、别叫、不许叫 | `EVT_VOICE_COMMAND_QUIET` / `CMD_QUIET` |
| 5 | HOLD_POSITION | 等等、定、别动、停、不准动、不许动、老实点、不可以、No | `EVT_VOICE_COMMAND_HOLD_POSITION` / `CMD_HOLD_POSITION` |
| 6 | FOLLOW | 走、跟我走、跟着我、跟上、跟我来 | `EVT_VOICE_COMMAND_FOLLOW` / `CMD_FOLLOW` |
| 7 | COME | 过来、来、回来、来这、到我这儿来、走上前来 | `EVT_VOICE_COMMAND_COME` / `CMD_COME_HERE` |
| 8 | APPROACH | 再过来点、再过来些、靠近点 | `EVT_VOICE_COMMAND_APPROACH` / `CMD_APPROACH` |
| 9 | MOVE_FORWARD | 往前走走、前进、向前走 | `EVT_VOICE_COMMAND_MOVE_FORWARD` / `CMD_MOVE_FORWARD` |
| 10 | BACK_UP | 往后退、靠后、退后、后退、往后稍稍 | `EVT_VOICE_COMMAND_BACK_UP` / `CMD_BACK_UP` |
| 11 | TURN_LEFT | 向左转、往左转、转到左边来 | `EVT_VOICE_COMMAND_TURN_LEFT` / `CMD_TURN_LEFT` |
| 12 | TURN_RIGHT | 向右转、往右转、转到右边来 | `EVT_VOICE_COMMAND_TURN_RIGHT` / `CMD_TURN_RIGHT` |
| 13 | SPEED_UP | 走快点、快点走、快跟上 | `EVT_VOICE_COMMAND_SPEED_UP` / `CMD_SPEED_UP` |
| 14 | SLOW_DOWN | 走慢点、慢点走、慢慢走 | `EVT_VOICE_COMMAND_SLOW_DOWN` / `CMD_SLOW_DOWN` |
| 15 | SIT | 坐、坐下、蹲下 | `EVT_VOICE_COMMAND_SIT` / `CMD_SIT` |
| 16 | LIE_DOWN | 趴、躺、趴下、躺下 | `EVT_VOICE_COMMAND_LIE_DOWN` / `CMD_LIE_DOWN` |
| 17 | STAND_UP | 站起来、起来、站好、站着 | `EVT_VOICE_COMMAND_STAND_UP` / `CMD_STAND_UP` |
| 18 | LOOK_DOWN | 低头、看地板、头低一点 | `EVT_VOICE_COMMAND_LOOK_DOWN` / `CMD_LOOK_DOWN` |
| 19 | LOOK_UP | 抬头、往上看、看上边 | `EVT_VOICE_COMMAND_LOOK_UP` / `CMD_LOOK_UP` |
| 20 | BARK | 叫两声、汪两声、说话 | `EVT_VOICE_COMMAND_BARK` / `CMD_BARK` |
| 21 | SHAKE_HAND | 握手、握个手、抬手、握手手、手手 | `EVT_VOICE_COMMAND_SHAKE_HAND` / `CMD_HAND` |
| 22 | HIGH_FIVE | 击掌、击个掌、拍手 | `EVT_VOICE_COMMAND_HIGH_FIVE` / `CMD_FIVE` |
| 23 | SPIN | 转圈、转一圈、转个圈、转一转 | `EVT_VOICE_COMMAND_SPIN` / `CMD_SPIN` |
| 24 | DROP | 吐掉、放下、松开、松口、张嘴、不可以吃、不可以咬 | `EVT_VOICE_COMMAND_DROP` / `CMD_SPIT` |
| 25 | CIRCLE_AROUND | 绕圈、绕着转一圈、绕一圈 | `EVT_VOICE_COMMAND_CIRCLE_AROUND` / `CMD_CIRCLE_AROUND` |
| 26 | PLAY_DEAD | 装死、倒下来 | `EVT_VOICE_COMMAND_PLAY_DEAD` / `CMD_DEAD` |
| 27 | SOCIAL_GREETING | 哈喽、你叫什么名字、你叫啥、你在干嘛、你听得懂吗、你会说话吗、你会说什么 | `EVT_VOICE_COMMAND_SOCIAL_GREETING` / `CMD_SOCIAL_GREETING` |
| 28 | SLEEP | 困了吗、去睡觉、睡吧、去休息、回窝睡觉 | `EVT_VOICE_COMMAND_SLEEP` / `CMD_SLEEP` |
| 29 | FOOD_CARE | 想吃啥、想吃不、喝水吗、吃不吃、吃罐罐 | `EVT_VOICE_COMMAND_FOOD_CARE` / `CMD_FOOD_CARE` |
| 30 | PLAY | 来玩、跟我玩、玩不玩、一起玩、玩游戏 | `EVT_VOICE_COMMAND_PLAY` / `CMD_PLAY` |
| 31 | PLAY_END | 不玩了、不想玩了、玩累了、结束游戏、下次再玩 | `EVT_VOICE_COMMAND_PLAY_END` / `CMD_PLAY_END` |
| 32 | GO_OUT | 出去玩、出门溜溜、一块出去走走、想不想出去玩 | `EVT_VOICE_COMMAND_GO_OUT` / `CMD_GO_OUT` |
| 33 | TUG_OF_WAR | 来拔河、拔河游戏、看谁力气大 | `EVT_VOICE_COMMAND_TUG_OF_WAR` / `CMD_TUG_OF_WAR` |
| 34 | CHASE | 追逐游戏、来追我呀、来抓我呀、抓小狗游戏、看谁跑的快 | `EVT_VOICE_COMMAND_CHASE` / `CMD_CHASE` |
| 35 | HIDE_AND_SEEK | 来捉迷藏、躲藏游戏 | `EVT_VOICE_COMMAND_HIDE_AND_SEEK` / `CMD_HIDE_AND_SEEK` |
| 36 | FETCH_FRISBEE | 飞盘游戏 | `EVT_VOICE_COMMAND_FETCH_FRISBEE` / `CMD_FETCH_FRISBEE` |
| 37 | CHASE_BALL | 去追球 | `EVT_VOICE_COMMAND_CHASE_BALL` / `CMD_CHASE_BALL` |
| 38 | PRAISE | 真棒、好狗、真乖、真聪明、可爱、厉害、太棒了、好样的、我爱你、我喜欢你、你真有趣、真有意思、小跟屁虫、小宝贝、乖狗狗、好狗狗 | `EVT_VOICE_COMMAND_PRAISE` / `CMD_PRAISE` |
| 39 | SCOLD | 你怎么不听话、不跟你玩了、不理你、不想理你、讨厌你、我生气了、我要惩罚你咯、罚你站着、罚你不准吃饭、坏狗狗、傻狗、笨狗 | `EVT_VOICE_COMMAND_SCOLD` / `CMD_SCOLD` |
| 40 | OWNER_UNHAPPY | 累、烦、不开心、不得劲、难过、心情不好、不舒服、孤独、压力大、难受、哭唧唧、悲伤 | `EVT_VOICE_COMMAND_OWNER_UNHAPPY` / `CMD_OWNER_UNHAPPY` |
| 41 | OWNER_HAPPY | 开心、高兴、幸运、耶、太好了、太妙了 | `EVT_VOICE_COMMAND_OWNER_HAPPY` / `CMD_OWNER_HAPPY` |
| 47 | OWNER_RETURNED | 我回来啦、想我了吗、回来咯、猜猜我是谁、谁回来啦、谁来啦、我来啦 | `EVT_VOICE_COMMAND_OWNER_RETURNED` / `CMD_OWNER_RETURNED` |
| 48 | OWNER_GOING_OUT | 我走啦、我出门啦、我出去啦、我出去一下、我去上班啦、我撤退啦、我先溜啦、我回去啦、拜拜、再见、乖乖等我回来哦 | `EVT_VOICE_COMMAND_OWNER_GOING_OUT` / `CMD_OWNER_GOING_OUT` |
| 49 | WHAT_IS_THIS | 你看看这是啥、快看、看过来、瞧瞧这是啥、你瞅瞅这是啥、这是什么、这是啥、那是啥、那是什么、这是什么东西、看前边 | `EVT_VOICE_COMMAND_WHAT_IS_THIS` / `CMD_WHAT_IS_THIS` |
| 50 | WHO_IS_IT | 看、看看谁回来了、看下谁来了、这是谁、我是谁、你知道我是谁吗、他是谁、谁呀、什么人 | `EVT_VOICE_COMMAND_WHO_IS_IT` / `CMD_WHO_IS_IT` |

## 参数、兜底及声源入口

| Excel行 | 路由 | 识别内容 |
|---|---|---|
| 42 | FETCH | [去拿xx/拿xx过来/找xx过来/找xx来]<br>xx为狗绳/狗绳子、玩具、狗碗等物品 |
| 43 | FIND_PERSON | 去找xx<br>xx为录入身份信息的人 |
| 44 | GO_TO_LOCATION | [去xx/到xx去/回xx去]<br>xx为狗窝、房间、卧室、客厅、厨房等有地图标记的地点 |
| 45 | FETCH_FROM_LOCATION | [去xx/到xx去/回xx去]+[去拿yy/拿yy过来/找yy过来/找yy来]<br>xx为狗窝、房间、卧室、客厅、厨房等有地图标记的地点<br>yy为狗绳/狗绳子、玩具、狗碗等物品 |
| 46 | CHAT_FALLBACK | 无明确意图及匹配词的闲聊内容 |
| 51 | SOUND_SOURCE | 1、唤醒词<br>2、高于周围环境或谈话声音频率的声音如[嘬嘬嘬]、[嘿嘿嘿]、拍手声、突发异响等 |

## 验收边界

- 逐条验证标准词句及变体的整句事件；同一句不能同时触发特技与问候。
- 否定、引用、第三人陈述及多目标不得靠截取子串执行；明确的停止游戏、责备与制止语句允许整句命中。
- 去厨房/门口拿狗绳子须同时保留地点与物品；泛称玩具通过 object_group=toy 授权下游选择目标，未注册人物、未知地点/物品仍为不可执行摘要。
- 去追球使用 CHASE_BALL 独立事件，与追人 CHASE 和取物 FETCH 分开；下游需接入追球及随机放球流程和5分钟任务上限。
- 声源行仅对齐通用声音活动入口，不证明拍手、嘬声或异响分类已实现；闲聊仍为非执行 UNKNOWN。
- 观察同一 utterance_id 的 command_lexicon、recognition_arbitration、event_publish；词库加载成功不等于真机验收。

## 表格全局约束（下游实现）

全局约束：
1、注意移动速度使用全局速度变量，速度给定范围，非固定某速度，演示速度范围为[0.3-0.5]
2、通用动作序列：正常人主动发起的唤醒后或交互后，指定完单次任务后，10s内无其他输入（视觉触觉听觉）均[抬头左右看观察人]，30s内无语音/视觉任务识别输入，随机选择平静动作池[坐下,趴下,转圈,走开,挠头,舔鼻子,抖毛]
3、找人逻辑：用哨兵模式感应人是否在附近，如在附近则启动声源定位朝向人，用视觉识别人，往人跟前走，移动到人跟前50-100cm处停下，不会每次都在同一个固定位置
4、有随机执行的动作序列，且具备分类属性的动作不单一调用，放在动作分类列表池中随机获取
5、所有动作序列在单次与人互动中不得重复超过3次（无互动半小时判断单次互动结束）
