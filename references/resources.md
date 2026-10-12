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
    {"name":"已验证文字组件","role":"caption","component_path":"components/产品名.json","verified":true}
  ],
  "text_styles":[],
  "text_animations":[]
}
```

catalog内path/cache_path/component_path和嵌套style资源路径可相对该catalog文件；载入时解析为绝对路径。字体写入每个styles.font及顶层font_path。字幕/音乐/效果依赖随后复制进新草稿，不能只改一个路径字段后声称完整迁移。

## 三类效果不能混用

1. caption_animations：原生type=caption，使用真实词时间；逐词显色/下划线不是普通TextIntro。
2. flowers：同条字幕的字效。需要effect_id、cache_path、**完整style**（字体、纹理/渐变、描边等），不是一个ID即可。style来自已验证材料。
3. text_templates：完整原生模板组件，包含片段、子文字、贴纸/动画和其它材料依赖；通过component_path引用。模板ID不等于内部花字ID。

v0.5另有text_styles（完整直接文字样式配方）与text_animations（原生in/out/loop动画）。roles支持caption/headline/subheadline/side_benefit/opening_hook/price_badge；semantic_roles可限定适用语义，family限定兼容包装族。花字旧role=inline兼容caption。模板的max_chars可省略；不是默认13字。

默认error策略在缺少策略所需资源时停止；所有变体在复制媒体前按实际种子预编译。用户接受普通方案后可设missing_effect_policy=plain并报告降级。semantic模式可通过角色策略明确只使用border；旧inline_emphasis_mode仅控制legacy模式。

## 包内提取工具

v0.5.7：字体候选排除旧草稿materials和JianyingPro Drafts路径，排除记录保留在excluded_draft_fonts；同ID/同名字体优先Cache/effect资源目录。绑定同样禁止草稿字体（含显式路径），应选择缓存或独立字体文件。默认关闭自动口播模板及词级字幕动画，避免未提供资源时主动请求；包装原生动画与字幕花字不关闭。

v0.5.6字幕花字池在general-conversion/caption-flowers.json，由native-style-recipes.json引用。35个ID按用户提供的角色分组，binding生成可执行flowers[].style（得意黑、用户指定金色、黑描边及实际字效路径），不是只有ID。此金色是显式配置，不冒充从原工程重建的全部纹理/渐变；已有完整导出style优先保留。局部字号随基础字幕；对象/人群/机会使用显式兼容池。绑定报告同时列bound_caption_flowers和missing_caption_flowers；缓存存在不会自动verified，仍支持显式未预览小样开关。

v0.5.5：resources输出flowers（artistEffect目录候选）与text_animations（已确认配方引用的动画）。无完整style的发现项binding_only=true，只用于绑定；配方完整style继续执行校验，不能把ID和目录当独立花字。用户可明确使用validate/plan/run --allow-unverified（或job.allow_unverified=true）生成未预览小样：默认仍拒绝，显式开关只免视觉预览条件，不免实际路径、依赖、完整样式和词时间检查；报告记录未验证，原catalog不被写成verified=true。

7套原生包装绑定成功不等于口播字幕的caption_animations、text_templates和price_badge资源完整；这些仍按实际策略检查，缺少时选择明确关闭相应策略或提供资源，不以放松包装校验掩盖口播组件缺失。

v0.5.4：字体缓存扫描取消原先20000条效果目录上限，先查预设ID目录、再完整扫.ttf/.otf/.ttc；直接放在ID层和嵌套hash层均支持。resources输出preset_font_inventory（ID、目录、字体文件）、font_scan和scan_errors用于诊断；名称筛选同时匹配文件名、内部名称和aliases。目录存在却未收录时先查错误，不判成未下载。

v0.5.3：字体扫描会从Cache/effect或Cache/font的紧邻数字ID目录提取resource_id并记录cache_directory_id证据。preset_resources也支持resources输出的effects列表：只把预设明确的字效ID绑定artistEffect缓存、明确的文字动画ID绑定effect缓存；目录须实际存在且非空。不改变已确认ID、不自动下载、不将绑定等同于视觉verified。旧草稿materials字体仍可按名称/明确路径绑定。

输入必须是已授权、本地明文工程；加密先用history_style.py调用包内本机DLL解密。不会下载上游工具。

```powershell
python scripts/component_tools.py extract --draft D:/任务/明文工程.json --segment-id 实际模板片段ID --out D:/本机组件/模板/component.json
python scripts/component_tools.py extract-flower --draft D:/任务/明文工程.json --segment-id 实际字幕片段ID --style-index 1 --out D:/本机组件/花字/catalog.json
python scripts/component_tools.py extract-caption-animation --draft D:/任务/明文工程.json --segment-id 实际字幕片段ID --animation-index 0 --out D:/本机组件/字幕动画/catalog.json
python scripts/component_tools.py extract-text-style --draft D:/任务/明文工程.json --segment-id 实际标题片段ID --role headline --family manual-general --out D:/本机组件/标题/catalog.json
python scripts/component_tools.py extract-text-style --draft D:/任务/明文工程.json --segment-id 实际竖排片段ID --role side_benefit --out D:/本机组件/侧栏/catalog.json
python scripts/component_tools.py extract-text-animation --draft D:/任务/明文工程.json --segment-id 实际字效片段ID --role caption --out D:/本机组件/文字动画/catalog.json
python scripts/preset_resources.py --catalog D:/任务/resources-selected.json --out D:/任务/resources-bound.json
python scripts/component_tools.py audit --draft D:/任务/明文工程.json --out D:/任务/原始资源引用报告.json
python scripts/component_tools.py validate --component D:/本机组件/模板/component.json
```

默认把资源复制为提取目录下的相对路径；--reference-resources明确引用原本机缓存。缺资源报错，不能编造路径。多个效果而未指明选择时工具要求具体索引，不随意挑一个。

完整模板只支持primary/mirror同文槽位，所有子文字必须声明并替换；不同文案多槽模板明确拒绝，避免残留旧价格或删错信息。提取时删历史正文/识别词/ID引用中的非依赖用户文本，保留必需样式与布局；遇到不能安全清理的内容报错。

新模板提取保存无文案的reference_text_width，换字时调整子文字框宽度，避免沿用短词固定框造成强制换行。按词模板必须回填当前真实words，不保留历史识别时间。直接样式导出支持分区底色/描边、逐行配方、独立字体、位置和原生文字动画；--base-style-index/--accent-style-index可明确新文字的基础/强调样式映射，--panel-segment-id可一起保留实际底板尺寸和颜色。

内置7套native-style-recipes.json只有已去文案、去路径的样式定义。字体通过已记录ID、精确内部字体名（含本地化名称）或显式路径绑定；哈希命名.ttf不会被当成字体名。resources解析字体name表及草稿content字符串内的styles.font，合并时保留ID证据；没有ID仍可使用本机字体，不伪造云端ID。默认名称对照见presets/font-identities.json。TTC只识别编译器可用的默认face，不假装可选择另一个face。

同名但文件内容不同会报歧义，不能随机选。可在catalog里设置 `font_bindings: {"所请求ID": {"path":"D:/字体/已确认文件.ttf"}}` 明确指定，若是替代字体须先确认风格变化。字效和文字动画按各自资源类别绑定，同一个ID不能跨类别冒充。绑定明细、缺项/歧义在.binding-report.json；新绑定verified=false，已有verified配方保留并单独列出。没有文件不等于ID过期。

audit输出原文件SHA256、已引用字体/字效以及文字→模板子项→material_animations的证据。text_segments.animations可能只是某个分析器的派生字段，不能只看它是否为空；sticker_animation是共享素材容器，需看引用者及子动画type。未提供的另一台机器不能声称已经验证。不会为了“凑35种字效”把未验证效果或错误角色加入随机池。

花字/原生动画导出是可合并到resource_catalog的JSON集合。导出结果verified=false；首次先在剪映原生界面选该资源手工改字并播放检查，或检查目标版本可正常播放的已有人工来源，再根据真实记录设置verified和版本。之后用本工具生成两条小样验证整套编排。没有首次资源验证时停止，不提前谎写true破解前置条件；文件存在不等于验证。

preferred-resources.json仅供检索名字与候选ID，不包含资源文件、许可证或验证承诺。字体/音乐未知名称保留原名，不把音效Cache/music路径当BGM；material.type=sound和music分别分类。Cache/music里没有类型证据的文件列为audio_candidates，展示后人工分类，绝不自动混入BGM。空BGM集合必须解决或明确music_enabled=false。

历史组件、本机路径、客户文案、字体/音乐/效果缓存只能存本机预设库或任务目录，不进入公开仓库。
