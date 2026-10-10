# 资源候选与白名单

resources 输出是发现结果，不是执行 catalog。AI 展示资源后，将用户选中且可用的资源写入任务目录的 resources-selected.json。运行时只使用这个文件或默认空 catalog。

```json
{
  "schema_version":"1.0",
  "verified_jianying_version":"实际测试的剪映版本",
  "fonts":[{"name":"微软雅黑","path":"C:/Windows/Fonts/msyh.ttc"}],
  "music":[{"name":"用户选中的音乐","path":"D:/音乐/轻快.mp3"}],
  "sfx":[{"name":"轻提示","path":"D:/音效/提示.wav"}],
  "text_intro":[{"enum":"渐显","verified":true}],
  "flowers":[{"effect_id":"从人工工程读取的实际效果ID","verified":true,"cache_path":"实际缓存目录"}],
  "video_intro":[],
  "video_outro":[]
}
```

字体可选 enum，对应后端 FontType。字体文件路径在草稿序列化后写入 font_path，具体剪映版本需检查是否实际生效。名称表、文件名和目录 ID 不能代替实际字体/效果验证。花字 effect_id 应是后端 add_effect 使用的 effect_id，不能混同 resource_id；无缓存路径时可能依赖剪映下载，交付报告需要说明。

音乐和音效必须本地可读，执行时不调用云端曲库下载。资源名未知可显示缓存文件名，不根据哈希编造曲名。近期草稿里音频可能是配音而不是音乐，展示前结合轨道或试听确认，不直接自动纳入背景音乐集合。

backend data/ 的 CSV 可检索枚举候选；完成首次小样验证后才把相应效果纳入 catalog。版本变更时 catalog 的 verified_jianying_version 不自动有效，应重检效果。

实拍切换默认硬切。第一版支持 VideoSegment 出入场动画而非跨空隙的相邻转场；不要承诺 random transition 已实现。若使用叠化转场需额外实现连续覆盖片段并验证 overlap。默认只使用少量文字动画，用户要求高密度时调整参数，不扩大未经验证的资源集合。
