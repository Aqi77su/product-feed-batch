# 产品信息流批量剪辑 Skill

将已有数字人口播与产品或服务补充素材批量编排为**可编辑剪映草稿**。一个批次一个工程，成片依次排在主时间线上，按编号/时间索引人工审核导出。

## v0.5：默认预设、原生包装与字幕修复

加载即启动通用预设，**顶部标题与侧栏默认开启，实拍原声默认关闭**。AI一次校正文案，标关键词角色、实拍窗口、人物优先区和包装文案；程序生成所有变体。默认不绑定商品、价格或私人素材。

- 按词字幕模板回填本次词时间；关键词接替自动拆前文/模板/后文，不重复叠整句字幕。
- 保护完整关键词与数字；按实际选中样式测宽，取消默认13字硬限；开场卡独立字体、逐行样式和位置。
- 解释型/转化型字幕策略、按角色随机、强调间隔和全篇覆盖；原位白字红描边、白/金基础字幕、直接字效动画、错位短价格徽章。
- 实拍在语义窗口内选素材，尊重标签、字幕/源段边界和保护区；长讲解窗与多卖点短窗都可用。
- 默认1.1倍口播，统一映射媒体/字幕/词时间；每条一首音乐、源0秒、不循环、尾淡出1.6秒、音量0.36。
- 7套去正文、去路径的原生样式定义，可绑定本机资源：顶部原生字效/模板、匹配底板、阳华体侧栏与循环动画、俪金黑开场卡；底部左图右文按输入启用。
- 默认缺请求的原生组件时报错；只有显式plain策略才降级并报告。
- 授权历史分析、模板/花字/字幕动画组件提取、可选本地预设和回归测试。

默认预设：[`general-conversion`](presets/general-conversion/preset.json)。它可直接由 `job.preset="general-conversion"` 选择，未指定也默认启用。显式job参数优先。

## 使用

将仓库安装至支持Skill的工具目录，例如：

```powershell
git clone https://github.com/Aqi77su/product-feed-batch.git "$env:USERPROFILE\.codex\skills\product-feed-batch"
```

说“加载 $product-feed-batch，先检查环境，再准备本批素材和数量”。首次加载检查剪映/Python/Node/FFmpeg与私有依赖，缺项先询问安装，不问例行输出地址。

流程：[SKILL.md](SKILL.md) · [v0.5风格契约](references/style-v05.md) · [任务协议](references/job-format.md) · [语义标注](references/semantic-style.md) · [资源组件](references/resources.md) · [验收](references/acceptance.md)。

数字人生成、HTML界面、自动导出MP4不在此包内。字幕工具会上传音频至字节云端，批次确认前说明；已有字幕可离线导入。工程与字幕源码已在vendor内，运行时不下载上游Skill。

## 资源与验证边界

v0.5.2 修复本地字体绑定：解析字体内部名称、恢复草稿嵌套JSON中的字体ID，支持哈希文件名、精确名称与显式路径；新增原始资源引用审计。缺本机文件不自动判为云端ID过期，也不因共享sticker_animation容器误删文字动画。

商业字体、音乐、效果缓存不随公开仓库分发。内置原生样式定义可通过 `scripts/preset_resources.py` 绑定本机catalog，输出缺失资源清单；绑定/提取不会自动标记verified，也不上传人工工程。首次须在目标剪映验证完整样式后启用。

已提供纯逻辑及真实FFmpeg合成素材的端到端草稿测试。它们验证结构、时间映射、替换、资源引用和边界，**不能证明剪映实际渲染或所有版本的草稿注册兼容性**。首次真实生产先生成两条小样并检查。

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

端到端测试需要requirements.txt中的依赖，在临时目录生成和清理测试媒体，不访问用户素材或调用云端ASR。

## 许可

自有代码/文档MIT。内置工程工具含MIT/Apache-2.0组件；内置字幕工具GPL-3.0-only。原许可证与源码保留，见[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本仓库不授予字体、音乐、剪映效果或软件的额外使用权。
