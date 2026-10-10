# 成片模式与组合（v0.2）

识别阶段执行 detect-mode，输入 job.scripts=[{id,text}]（AI先拆分用户混贴文案）。程序归一化中英文/数字并用字符序列匹配提出 full/section 覆盖范围、置信度、备选文案、遗漏警告。覆盖全文提出 single；连续段覆盖全文提出 splice；低置信、歧义、缺段/重叠提出 uncertain。程序建议不能代替用户确认。数字格式差异、重复句和文案版本需要 AI 校正后再检测。

用户确认前展示每篇文案、段落原文、候选源、剔除项、条数和组合池。组内为同文案同段的不同数字人，组间按文案顺序接续。一次任务可包含多个拼接脚本组；单人脚本可用只有一个 section 的组表达，支持混合批次。未提供文案不能自动推断完整覆盖，询问单人/拼接关系。

```json
{
  "scripts":[{"id":"script_01","text":"完整原文"}],
  "assembly":{
    "mode":"splice",
    "confirmed":true,
    "allow_duplicate_combinations":false,
    "groups":[{
      "script_id":"script_01",
      "script_length":8,
      "count":16,
      "sections":[
        {"section_id":"part1","char_start":0,"char_end":4,"sources":["main_a","main_b","main_c","main_d"]},
        {"section_id":"part2","char_start":4,"char_end":8,"sources":["main_e","main_f","main_g","main_h"]}
      ]
    }]
  }
}
```

char_start/end 以 normalize_script 保留字母数字后的完整文案计算，段覆盖无缺口、不重叠。script_length 为该归一化全文长度。多个组 count 合计为 job.count。每源 corrected.json 仍保留实际口播、script_id 和 section_id；不能补上源没有读出的文字。执行器使用选中源自己的字幕，按每段实际时长偏移，不按文案估算时间。

组合池是各 section 候选数乘积；默认无放回抽完整组合，跨条可重复使用单个源。条数超过组合池拒绝，只有用户确认 allow_duplicate_combinations 才允许重复完整组合。各组均保留组合编号、源序列及随机种子。

头尾禁插区按整条成片计算，不按每个源计算。broll_boundary_policy：allow 允许跨段；prefer_cover（默认）优先覆盖内部换人点；within_segment 禁止跨内部段边界。实拍不改变主音频时间。

默认 broll_mode=count、broll_count=[1,1]、duration=[5,15]，不是硬性40%-50%覆盖。ratio 模式才以 broll_ratio=[0.4,0.5] 为目标，空间不足仍记录实际覆盖。短于最小时长的实拍默认skip；allow允许短片段。素材和可插空间不足时减少数量，记录实际计划；不变速/冻帧填充。

默认字幕：y=-0.39，字号13，金色[1,0.87,0]，基础字体仅从得意黑/俪金黑选择。字体找不到时先展示缺项，不将198字体全池当默认。指定catalog的其他字体代表用户主动覆盖。

默认音乐：0.36、从0秒开始、不循环、无淡入淡出。music_short_policy=reject：候选短于成片则排除，全无足够长候选时停止并询问；allow_silence明确允许播完后留静音。music_loop=true可恢复循环，fade参数单位秒。不同音乐响度不同，0.36不保证听感一致。

描边参数 subtitle_border_width 是后端0-100单位，默认40；当前后端 width/100*0.2，因此40序列化为0.08，不要把0.08直接传给接口。
