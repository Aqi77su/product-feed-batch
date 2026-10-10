# 资源、原生动画和完整文字组件

resources发现结果不是执行白名单。选择本机真实资源；原生效果/模板需verified=true及catalog.verified_jianying_version。结构验证不能替代剪映打开验证。商业资源不随公开仓库发布。

```json
{
  "schema_version":"1.0",
  "verified_jianying_version":"用户实际验证的版本",
  "fonts":[{"name":"得意黑","path":"D:/资源/得意黑.ttf","resource_id":"本机实际ID"}],
  "music":[{"name":"已选曲目","path":"D:/音乐/曲目.mp3"}],
  "sfx":[{"name":"已选提示","path":"D:/音效/提示.wav"}],
  "caption_animations":[
    {"name":"逐词显色","resource_id":"7366166936868098579","cache_path":"resources/caption","verified":true}
  ],
  "flowers":[],
  "text_templates":[
    {"name":"已验证文字组件","role":"caption","component_path":"components/产品名.json","max_chars":13,"verified":true}
  ]
}
```

catalog内path/cache_path/component_path和嵌套style资源路径可相对该catalog文件；载入时解析为绝对路径。字体写入每个styles.font及顶层font_path。字幕/音乐/效果依赖随后复制进新草稿，不能只改一个路径字段后声称完整迁移。

## 三类效果不能混用

1. caption_animations：原生type=caption，使用真实词时间；逐词显色/下划线不是普通TextIntro。
2. flowers：同条字幕的字效。需要effect_id、cache_path、**完整style**（字体、纹理/渐变、描边等），不是一个ID即可。style来自已验证材料。
3. text_templates：完整原生模板组件，包含片段、子文字、贴纸/动画和其它材料依赖；通过component_path引用。模板ID不等于内部花字ID。

默认error策略在请求资源缺失时停止，不让随机抽签决定这次是否碰巧失败。用户接受普通方案后可设missing_effect_policy=plain，或者显式caption_animation_mode=none、inline_emphasis_mode=border、template_probability=0。后者是清楚指定的基础风格，不能报告为完整花字复刻。

## 包内提取工具

输入必须是已授权、本地明文工程；加密先用history_style.py调用包内本机DLL解密。不会下载上游工具。

```powershell
python scripts/component_tools.py extract --draft D:/任务/明文工程.json --segment-id 实际模板片段ID --out D:/本机组件/模板/component.json
python scripts/component_tools.py extract-flower --draft D:/任务/明文工程.json --segment-id 实际字幕片段ID --style-index 1 --out D:/本机组件/花字/catalog.json
python scripts/component_tools.py extract-caption-animation --draft D:/任务/明文工程.json --segment-id 实际字幕片段ID --animation-index 0 --out D:/本机组件/字幕动画/catalog.json
python scripts/component_tools.py validate --component D:/本机组件/模板/component.json
```

默认把资源复制为提取目录下的相对路径；--reference-resources明确引用原本机缓存。缺资源报错，不能编造路径。多个效果而未指明选择时工具要求具体索引，不随意挑一个。

完整模板只支持primary/mirror同文槽位，所有子文字必须声明并替换；不同文案多槽模板明确拒绝，避免残留旧价格或删错信息。提取时删历史正文/识别词/ID引用中的非依赖用户文本，保留必需样式与布局；遇到不能安全清理的内容报错。

花字/原生动画导出是可合并到resource_catalog的JSON集合。导出结果verified=false；首次先在剪映原生界面选该资源手工改字并播放检查，或检查目标版本可正常播放的已有人工来源，再根据真实记录设置verified和版本。之后用本工具生成两条小样验证整套编排。没有首次资源验证时停止，不提前谎写true破解前置条件；文件存在不等于验证。

preferred-resources.json仅供检索名字与候选ID，不包含资源文件、许可证或验证承诺。字体/音乐未知名称保留原名，不把音效Cache/music路径当BGM；material.type=sound和music分别分类。Cache/music里没有类型证据的文件列为audio_candidates，展示后人工分类，绝不自动混入BGM。空BGM集合必须解决或明确music_enabled=false。

历史组件、本机路径、客户文案、字体/音乐/效果缓存只能存本机预设库或任务目录，不进入公开仓库。
