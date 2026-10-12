# v0.5 字幕与默认包装契约

## 默认即启用

加载 Skill 就使用 general-conversion；顶部标题 headline_enabled=true、侧栏 side_enabled=true、实拍 broll_volume=0。无需用户手填 JSON 或额外要求启用。标题/侧栏没有合法内容时，AI从本批确认过的稿子提炼，随整批确认一起回显；脚本不会虚构价格，也不会悄悄省掉默认项。

`layout.headline_enabled=false`、`layout.side_enabled=false` 分别关闭；`layout.enabled=false` 关闭自动包装。`start_policy=after_opening_hook` 默认绕过开场卡，composition_start 可覆盖。顶部、侧栏都以整条成片为作用域，不随换人重启。图片/声明保持独立、按需。

## AI 一次分析，程序生产所有变体

每篇 corrected JSON 可设 `caption_profile=explainer|conversion|auto`；自动模式依据已经标注的 price 类关键词判断，不读取画面猜产品。两份人工参考的解释型与促销型字幕密度不同，不把密集花字强套每类内容。

- explainer：基础白字与逐词显色；默认不密集强调。需要明确加强时设置 explainer_emphasis=true。
- conversion：按固定策略在白/金基础、局部白字红描边、局部/整句花字、短语模板和少量价格徽章中选择。原生动画包含逐词显色、下划线和无动画；直接花字也可配入场/出场/循环动画。
- 基础颜色＋基础字幕动画按整条成片选一次配套方案，保持本条普通字幕连贯；重点事件再改变局部表现，不逐句任意混抽基础颜色/动画。跨条变体可选另一套。
- 概率、角色兼容、1.5秒强调间隔、同短语8秒复用间隔及全篇三段覆盖均由程序处理。默认权重在 caption-policy.json；观察到的基础动画比例与工程化角色权重分别注明，不冒充学习出的精确概率。

关键词 role 支持 audience、opportunity、object、price、benefit、reassurance、action；旧 core_object、price_or_low_cost、ease_or_low_barrier 等兼容。标完整短语、数字与单位。可选 presentation=border|flower|whole_flower|template|badge，只在有明确要求时指定，通常交给程序。

## 一个口播主体

1. 普通或原位富文本：同一材料完整覆盖文字，保留非关键词基础样式。
2. 短语模板：依据真实词边界拆出前文、模板、后文。模板占用自己的时间，底层不复制整句；当前 words 回填每个同文模板子槽位，不能沿用历史词时间。
3. 价格徽章：独立的短价格信息，使用 price_badge 角色、已验证的错位位置；基础字幕继续，不能把整句抄进徽章。
4. 开场卡：仅明确 opening_hook_card 保留2–3行，按对应行数选择原生多行样式，保留俪金黑等独立字体、逐行字效、字号/缩放/位置。

缺真实词边界时，部分句模板请求改为原位描边并报告；word 模式或按词模板需要可靠 words。新词时间经源→倍速→成片→批次偏移只映射一次，不按旧模板时长再次缩放。

## 字号与断句

基础1080p得意黑14/1.15/y=-.3645833333；720p为13/1.15/y=-.390625。局部红描边默认白字，非全局强制金字。模板、开场卡和持久标题保留各自尺寸体系。

13个等效汉字不再是默认硬上限。程序选好实际样式后按各段字体、描边、字效留白测量；模板先用自己的内外层字体/变换评估，不能先用基础字幕把整句切碎。完整关键词和数字表达不可从中间拆断。超过安全宽度按真实词边界切为先后字幕；普通字幕不随意3+2换行，不用无限缩小字号绕过失败。

默认语义生成器没有可靠词时间时不会用等时估算冒充重断句；旧 approximate_equal 只在显式 legacy caption_policy 下兼容。静态测宽仍不是剪映动画中间态的像素验证。

## 原生顶部与侧栏

默认内置7套 native-style-recipes.json 来自两份授权参考的**样式定义**，已删除正文、源工程身份、路径和旧词时间。包含三极榜楷等标题、阳华体侧栏、俪金黑两/三行开场、原生分色/描边/字效/循环动画及配套浅绿底板。完整标题模板同样通过 text_templates 的 headline/subheadline 角色选择。

程序按 family 选择兼容组合，完整样式拥有自己的字体、字号、变换、动画和底板；不把字幕字体直接套给所有标题。副标题和底部声明仍需实际输入。侧栏中的数字可自动映射 accent 样式；非数字利益词可提供 accent_ranges，范围以本次文字为准。

商业字体和效果文件不随包发布。绑定命令：

```powershell
python scripts/preset_resources.py --catalog D:/任务/resources-selected.json --out D:/任务/resources-bound.json
```

脚本按已记录ID或精确字体内部名称绑定本机字体，也支持显式路径；不会把云端ID作为本地字体的唯一身份。字效/动画按对应类别ID绑定，输出缺失/歧义清单和匹配依据；新定义保持 verified=false，原生预览后启用。不能伪造 verified 或使用临时测试资源冒充真实资源。也可从已授权且当前版本可正常显示的工程提取新样式。

公开默认是可执行规则与可绑定样式定义，不含本机商业资源。缺少请求的原生资源默认提前报错；只有用户明确接受后才设置 plain 并标明降级，不能将普通字称作原生风格复刻。
