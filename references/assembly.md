# 成片模式与组合（v0.4）

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

头尾硬禁区按整条成片计算，不按每个源重复计算。默认broll_mode=semantic及boundary_policy=within_segment：先映射各源的语义窗口，再选素材，不跨换人边界。allow/prefer_cover仅作为用户显式旧模式兼容，不是通用风格。人物优先区、实际标签和完整字幕边界同样参与选择。

默认不规定每条只有一个5–15秒镜头。one_continuous_window适合连贯解释，short_sequence适合多个短镜头；机会可明确时长。数量、比例是软目标，空间/语义不足记录警告，不随处补镜头。count/ratio是显式旧模式。详见semantic-style.md。

默认主视频1.1倍速，源字幕先prepare再map：源时间÷速度＋该源目标偏移；写入工程才加全局成片偏移。相同映射幂等，不允许不同映射重复套用。字幕字号/位置按通用预设标定，普通字、花字和完整模板是单一主体的不同呈现。原文未读出的内容不得为凑全文新增。

默认音乐：0.36、从0秒开始、不循环、无淡入、尾淡出1.6秒，贯穿同条所有人物段。music_short_policy=reject排除过短候选；allow_silence需明确选择。显式music_loop可恢复循环；不同歌曲源响度不同，静态倍率不保证相同听感。

描边参数 subtitle_border_width 是后端0-100单位，默认40；当前后端 width/100*0.2，因此40序列化为0.08，不要把0.08直接传给接口。
