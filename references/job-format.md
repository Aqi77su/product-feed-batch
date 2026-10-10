# 任务和工具协议

所有路径使用绝对路径。下列是实际脚本支持的字段，未知字段不承诺生效。AI 应直接按此结构规范用户需求；不是让非专业用户填写此文件。

```json
{
  "schema_version": "1.0",
  "name": "产品批次001",
  "count": 100,
  "main_videos": "D:/素材/数字人",
  "broll": "D:/素材/实拍",
  "reference_video": "D:/参考/成品.mp4",
  "title": "用户确认的持续标题",
  "titles": {},
  "script_text": "多篇原始文案可粘贴在这里",
  "resource_catalog": "D:/任务/resources-selected.json",
  "subtitles": {},
  "overlays": [
    {"kind":"image","path":"D:/素材/产品信息.png","transform":{"scale_x":0.35,"scale_y":0.35,"transform_x":0,"transform_y":0.55}},
    {"kind":"text","text":"用户提供的广审文字","font_size":3,"y":-0.92}
  ],
  "settings": {
    "opening_no_broll_s":3,
    "ending_no_broll_s":3,
    "broll_ratio":[0.3,0.5],
    "broll_duration_s":[2,4],
    "seed":20261010
  }
}
```

`main_videos`、`broll` 可为目录、文件或路径数组。主视频按内容哈希去重；同一内容复制多份不会算成多个源。`count` 是本批总成片数。`distribution` 可选：source_id → 数量，必须包含所有 main ID，允许 0，合计必须等于 count。未指定时均匀分配。source_id 在 inspect 结果中取得。

日常不问 draft_root；工具从剪映配置和存在的默认目录探测，环境报告应显示选中目录及候选。多个候选时 AI 根据实际配置来源判断，不能随意选旧目录。`PFB_DRAFT_ROOT` 或 job 的 `draft_root` 仅用于检测失败后的显式环境适配/隔离测试，不作为日常用户问题。

基础字体/音乐优先用户默认清单；没有显式 catalog 时定位得意黑/俪金黑及常用音乐，字体找不到则要求确认替代，不从全池随机。没有音乐则在确认清单说明。指定资源把对应候选列表缩成一项即可。`settings` 支持字段见 presets/defaults.json；`main_transform` 和 `broll_transform` 用剪映 ClipSettings 坐标，正 y 向上，范围通常 -1 到 1。图层缩放是素材相对于工程尺寸的显示缩放；首轮必须检查实际画幅是否覆盖预期区域。

背景支持：`{"mode":"none"}`、`{"mode":"blur"}`、`{"mode":"image","path":"绝对路径"}`。image 背景在主画面下方，主画面默认全屏会遮住背景，需相应缩小 main_transform。无自动抠像；第一版未实现从数字人首帧自动生成背景，若用户要求可先通过 frames 导出一帧再传 image。

## 字幕结构

```json
{
  "schema_version":"1.0",
  "source_id":"main_源哈希前16位",
  "source_hash":"完整sha256",
  "status":"corrected",
  "script_id":"script_01",
  "correction_notes":[],
  "cues":[
    {
      "id":"s0001",
      "start_ms":40,
      "end_ms":1280,
      "text":"你真的太幸运了",
      "words":[],
      "keywords":[
        {"text":"幸运","char_start":4,"char_end":6,"start_ms":600,"end_ms":880,"timing_precision":"word"}
      ]
    }
  ]
}
```

字符索引是 Python Unicode 字符索引，末端不含；对校正后的 text 计算。words 保留实际识别结果，文本校正后不能伪称 words 已重新对齐。`reviewed_asr` 表示没有原文案，AI 已检查 ASR；status=raw 不允许生产。

job.subtitles 为 `source_id → corrected.json 绝对路径`。标题可在 titles 中按 source_id 覆盖；一个源的不同版本共享文案和标题，不用 AI 为每个变体写新标题。需要不同标题时后续扩展规则，不能凭空假设已支持。

## 命令

- `doctor --out environment.json`：只读环境检查，不安装。
- `setup --approved`：用户确认后建立私有 Python runtime，安装固定基础依赖；字幕源码和工程源码均已内置，不再下载上游包。
- `resources --out resources.json`：缓存与近期工程资源发现，不自动加入白名单。
- `inspect --job job.json --out inventory.json`：源文件盘点和默认数量分配。
- `frames --video reference.mp4 --out reference-dir --count 8`：抽帧及联系表。
- `transcribe --job job.json --work task-dir`：每个不同源识别一次，上传音频。
- `import-srt --video source.mp4 --srt existing.srt --out source.raw.json`：句级字幕输入。
- `validate --job job.json --out validation.json`：源哈希、字幕时间、关键词、catalog 校验。
- `plan --job job.json --out planned.json`：纯随机规划，不写工程。
- `run --job job.json --work task-dir --confirmed --serve --port 0`：生成新草稿，启用只读状态端口。
- `verify --draft draft-dir`：检查主轴、材料存在、每条边界、禁插区。

`--approved`、`--confirmed` 是 AI 在得到真实用户确认之后使用的执行开关，不代表自动授权。初次批次确认允许本流程提取并上传每个源的音频；说明后确认一次，不逐个重复询问。

工具失败返回非零退出码和 JSON error。部分 backend 自带日志可能和最终 JSON 同时出现，读取末尾结果或任务 status.json。默认端口随机分配并只监听回环；完成/失败后保留 5 秒查询窗，然后关闭，可继续读 status.json。

失败草稿保留 INCOMPLETE.json 和已生成文件供诊断，不覆盖、不自动删除。选择新名称重试；字幕成功缓存可复用，但第一版不是从任意半截草稿续写。所选素材/规则种子可复现，不承诺 UUID 相同。

## v0.2 模式与参数

使用 [assembly.md](assembly.md) 的 scripts/assembly 配置。detect-mode --job job.json --work task-dir --out mode-proposal.json 提出分组供用户确认。默认 count 模式每条1段实拍；ratio模式单独启用。音乐不循环时按整个成片时长筛选，长度不足默认reject。
