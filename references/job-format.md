# 任务和命令协议 · v0.4

AI负责把自然语言整理成下面结构，不要求非专业用户手填。路径使用绝对路径；内置preset名称除外。

```json
{
  "schema_version":"1.0",
  "name":"产品批次001",
  "preset":"general-conversion",
  "count":10,
  "main_videos":"D:/素材/口播",
  "broll":"D:/素材/补充",
  "broll_assets":[
    {"path":"D:/素材/补充/操作.mp4","tags":["product","operation"],"usable_ranges":[{"start_ms":0,"end_ms":12000}]}
  ],
  "scripts":[{"id":"script_01","text":"本批完整文案"}],
  "subtitles":{},
  "resource_catalog":"D:/任务/resources-selected.json",
  "layout":{
    "bundle":"auto",
    "headline":"用户确认的持续标题",
    "subheadline":"当前任务利益信息",
    "side_benefit":{"text":"当前任务短利益点","side":"right"},
    "product_image":"D:/素材/当前商品图片.png",
    "disclosure":{"text":"必要说明第一行\n必要说明第二行"}
  },
  "settings":{"seed":20261011}
}
```

layout里的槽位全部可省略；不替用户编内容。标题候选也可由corrected文档传入。bundle支持auto/plain/header/header_panel；auto在兼容整套布局中选择，有标题才创建顶部组件。side_benefit真实竖排，支持left/right。必要说明保留标点、换行。旧title/titles及overlays(kind=image/text)仍支持；不能同时重复配置同一信息。

main_videos/broll可为目录、文件或路径数组，按内容哈希去重。broll_assets用path或asset_id匹配盘点条目；tags须真实；usable_ranges为源绝对毫秒合法窗，[]表示没有可用区间。未提供broll但提供broll_assets路径时可直接盘点这些文件。

count是本批总条数。单人口播可用distribution（所有source_id→非负数量，合计count），默认均分。多人按[assembly.md](assembly.md)确认段组；同一个源可以跨成片复用，默认完整组合不重复。

## 预设与参数优先级

未指定preset时使用general-conversion，也支持default别名或本地preset.json绝对路径。优先级：内置运行默认 < preset.settings_file < preset.settings < job.settings。layout_defaults同样被job.layout覆盖。私人预设通过history_style.py创建，不自动改写。

通用默认：1080×1920/30fps、1.1倍口播、白色得意黑14号/scale1.15/y约-.365；720×1280自动采用13号/y-.390625，显式字号位置优先。其他画布需另行标定。所有支持字段见presets/defaults.json。

- main_speed、main_volume可覆盖；不照搬历史9–10倍音量。ASR永远声明实际源时基，程序统一缩放。
- broll_mode默认semantic；count/ratio只作为用户显式选择的旧兼容模式，报告会提示不使用语义窗。
- broll_boundary_policy默认within_segment；opening_no_broll_s/ending_no_broll_s默认0，语义人物优先区控制开头收尾，用户显式秒数是硬约束。
- broll_count为语义模式软目标范围；broll_duration_s是未在机会中指定时的长度范围，不允许为凑目标越界。
- music_enabled=true，空音乐池会阻止生产；用户明确关闭时设false。music_volume=.36，music_source_start_s=0，music_loop=false，music_fade_in_s=0，music_fade_out_s=1.6。短歌默认reject，明确allow_silence才播完留静音。
- caption_animation_mode=auto：有真实words时请求原生字幕动画，无words时记录跳过；word要求可靠words；none明确不用。
- inline_emphasis_mode=flower请求完整花字样式；border是合法的原位描边方案。template_probability默认.35，设0明确不用整句模板。
- missing_effect_policy=error默认拒绝缺请求资源；plain显式接受降级并记录。不能靠空catalog悄悄关闭效果。
- subtitle_long_cue_policy=error默认拒绝无可靠时间的长句；approximate_equal明确接受句级近似，绝非逐词对齐。
- keyword_popup_probability已废弃：不再生成复制的Emphasis轨，即使旧job设置它也只警告。
- sfx_mode=keywords默认跟实际重点事件；none关闭；every_cue只保留为显式旧选项。
- background默认none；image/blur仅显式任务启用，不自动抠人或随机背景。

日常不问draft_root，工具自动检测。PFB_DRAFT_ROOT/job.draft_root只用于明确的环境适配或隔离测试。

## 字幕和语义

job.subtitles映射source_id→corrected JSON绝对路径。每源识别一次，保存raw，校正、标关键词及实拍窗口后保存corrected。[semantic-style.md](semantic-style.md)给出完整字段和真实/近似精度规则。

新raw/import-srt声明timestamp_basis=source_media。旧文件缺时基会按源时间兼容但降级词精度，不能冒充校准过的words。run会额外保存prepared-subtitles，不覆盖用户corrected文件。

## 命令

- doctor --out environment.json：只读检测，不安装。
- setup --approved：确认后安装私有运行依赖；vendor源码已内置。
- resources --out resources.json：发现候选，不自动验证效果。
- inspect --job job.json --out inventory.json：真实文件、数量、资源和默认参数。
- frames --video reference.mp4 --out reference-dir --count 8：带时间抽帧/联系表。
- transcribe --job job.json --work task-dir：每源一次云端ASR，按哈希缓存；失败不无限自动重传。
- import-srt --video source.mp4 --srt source.srt --out source.raw.json：句级导入。
- detect-mode --job job.json --work task-dir --out mode-proposal.json：提出模式/段组，需确认。
- validate --job job.json --out validation.json：字幕、词时间、标签、语义和catalog。
- plan --job job.json --out planned.json：确定性的合法随机规划。
- run --job job.json --work task-dir --confirmed --serve --port 0：新工程生产及回环只读状态。
- verify --draft draft-dir：工程引用、时间、单主体、语义实拍和边界校验。

--approved/--confirmed记录真实用户授权，不能由工具开关替代。/status、/items、/events、/health仅本机查询，完成后5秒关闭；status.json继续可读。

失败返回非零和JSON error；保留INCOMPLETE供诊断。成功输出resolved-plan、segments-index.csv、build-report；不自动导出MP4，不自动覆写加密根注册信息。
