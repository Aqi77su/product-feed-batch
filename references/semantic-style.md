# 通用风格：语义先确定，变体由程序生成

## 判断的对象

不是“血压计模板”，而是跨商品/服务的叙事机制。一次处理整篇文案，复用到所有变体；不同数字人的源时间各自对齐。AI不为每条变体指定资源、镜头秒数或绝对成片位置。

关键词角色：人群/门槛、痛点/机会、核心对象、价格/低成本、使用利益、消除顾虑/文案已有依据、行动/紧迫性。选完整短语，保留数字限定和单位；不要每个名词或每次出现的产品名都强调。先收集整篇候选，再由概率和间隔选择，兼顾开头、中段、结尾。

短时关键词跟口播，title_candidates概括整条利益；两者不能互相冒充。不能依据历史工程为新商品新增功效、价格、资质或收益承诺。

## 源文档与实拍标注

所有时间都是未加速源文件的绝对毫秒，timestamp_basis=source_media。words支持ASR的startMs/endMs及规范化start_ms/end_ms，二者都必须为源绝对时间；不能把cue相对时间混进来。

```json
{
  "schema_version":"1.0",
  "source_id":"main_实际哈希前16位",
  "source_hash":"完整真实哈希",
  "status":"corrected",
  "timestamp_basis":"source_media",
  "script_id":"script_01",
  "cues":[
    {"id":"s1","start_ms":0,"end_ms":2000,"text":"先看一下","words":[],"keywords":[]},
    {"id":"s2","start_ms":2000,"end_ms":5000,"text":"这款产品操作简单","words":[],
     "keywords":[{"text":"操作简单","char_start":4,"char_end":8,"start_ms":2000,"end_ms":5000,"timing_precision":"cue","role":"ease_or_low_barrier"}]},
    {"id":"s3","start_ms":5000,"end_ms":8000,"text":"按一下就能使用","words":[],"keywords":[]},
    {"id":"s4","start_ms":8000,"end_ms":10000,"text":"了解后再决定","words":[],"keywords":[]}
  ],
  "broll_opportunities":[
    {"id":"b1","from_cue":"s2","to_cue":"s3","role":"operation_demo","priority":"high",
     "preferred_asset_tags":["operation"],"presentation":"one_continuous_window",
     "keep_within_source_segment":true,"min_duration_s":2,"max_duration_s":8,
     "reason":"两句连续介绍操作"}
  ],
  "talking_head_preferred":[
    {"from_cue":"s1","to_cue":"s1","priority":"soft","reason":"保留开场直述"},
    {"from_cue":"s4","to_cue":"s4","reason":"保留结尾直接交流"}
  ],
  "title_candidates":[{"text":"操作简单","from_cue":"s2","to_cue":"s2","semantic_role":"ease_or_low_barrier"}]
}
```

示例故意保留words空和cue精度，不是可冒充逐词时间的数据。标word精度必须有真实、完整、与校正文案一致的词时间；大幅改文案不能沿用错位的words。

原始关键词索引是Python字符[start,end)；清洗后程序重新映射。当前安全字幕范围为BMP，非BMP字符明确报错，不能静默丢失。长普通句可依真实词边界拆分；无词时间默认报错，人工/AI按真实口播重断句后再提交。只有明确设置subtitle_long_cue_policy=approximate_equal才作句级近似等时拆分，输出标记近似，禁用于原生词级动画。

明确的开场卡使用cue.layout_role=opening_hook_card，text包含2–3行完整短句。程序保留并逐行测宽；普通cue里的换行不自动变成开场卡。不要将短词硬分两行。

## 机会的类型和约束

- role：product_intro、product_usage_benefit、product_feature、operation_demo、audience_scene、price_offer、logistics、call_to_action、explanation、benefit_demo。
- 兼容别名：object_reveal、benefit_explanation、offer_logistics、action_reminder。
- priority：high/medium/low；presentation：one_continuous_window或short_sequence。
- from_cue/to_cue包含起止cue，必须真实存在且顺序正确；同源机会不能互相重叠，合并或拆成明确不重叠区域。
- preferred_asset_tags必须存在于本批broll_assets；匹配至少一个标签，优先匹配更多。不能把未知编号镜头标成已确认动作。
- keep_within_source_segment必须true。不跨成片/数字人段/用户硬禁区；当前人物优先区也保守避开，不为覆盖率强行退让。
- 窗口是候选而非必放点。无匹配素材、长度不足、被保护区扣除时留人物并报告。
- one_continuous_window适合连贯说明，可超过15秒；short_sequence允许同一语义段内多个短镜头。数量/比例是软目标，不是凑数命令。

有补充素材而缺broll_opportunities字段会报错。分析后确无合适点可显式给[]。源素材优先有效窗起点；只有broll_source_offset_mode=random才在有效范围随机裁取。

## 程序的时间与选择

prepare_document清洗/拆句并保留parent_cue_id和cue_aliases，语义锚点不会因拆句失效。map_document一次性执行“源时间÷速度＋该源在成片中的偏移”，随后写工程时只加批次全局偏移。

同文案变体共享角色、机会和匹配逻辑，使用各自真实源时间。种子控制合法窗口/素材/完整组件选择，音乐按批次使用次数均衡轮换。不能全时间轴盲抽实拍，再给它事后编一个语义解释。
