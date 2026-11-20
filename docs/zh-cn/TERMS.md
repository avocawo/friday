# 简体中文术语表

语气：中性的技术文档风格，参照国内优秀开发者文档的写法，不使用营销口吻。正文使用全角标点，中文与英文、数字之间加空格。

每个译文文件中使用的翻译说明（一句话）：

*本文是英文文档的译文；如与英文版有出入，以英文版为准。*

| English | 简体中文 | Note |
| --- | --- | --- |
| task | 任务 | 不保留英文。 |
| plan | 计划 | 不保留英文。 |
| milestone | 里程碑 | 不保留英文。 |
| stage | 阶段 | 计划的最小单位；`status` 中的 phase 译为“环节”，以免与 stage 混淆。 |
| criterion | 验收标准 | 复数同形；“code criterion”为“代码验收标准”，“behavior criterion”为“行为验收标准”。 |
| profile | 配置文件（profile） | 首次出现写“配置文件（profile）”，之后统一为“配置文件”；`profile` 作为命令参数时保持代码形式。 |
| owner | 负责人 | 指批准计划、验收 MR 的人；不译为“用户”或“操作员”。 |
| planner | 规划模型 | 指编写计划的模型；不保留英文。 |
| executor | 执行模型 | 指编写代码的模型；不译为“开发者”。 |
| reviewer | 评审模型 | 指审查计划或代码的模型。 |
| model tier | 模型档位 | 取值 `frontier`、`strong`、`efficient` 保持英文代码形式。 |
| role assignment | 角色分配 | 指把具体模型分配给任务各角色。 |
| gate | 关卡（gate） | 首次出现写“关卡（gate）”；中文开发者也常直接说 gate，后文可用“关卡”，配置键 `gate` 保持代码形式。 |
| milestone review | 里程碑评审 | 不保留英文。 |
| final review | 最终评审 | 不保留英文。 |
| blind check | 盲审 | 首次出现写“盲审（blind check）”；指在空目录中不带上下文的检查。 |
| browser acceptance | 浏览器验收 | 不保留英文。 |
| disputed finding | 有争议的发现 | 指执行模型不同意的评审意见。 |
| card | 任务卡 | 指 Friday 的机器状态记录；不译为“卡片”以免与看板混淆。 |
| journal | 日志 | 指面向人的可读历史；命令 `journal` 保持代码形式。 |
| worktree | worktree | 保留英文，中文开发者通常不翻译。 |
| draft MR | 草稿 MR | MR/PR 保留英文缩写；“草稿”对应 draft。 |
| pipeline | 流水线 | 不保留英文。 |
| vendor | 厂商 | 指模型提供商（如 Anthropic、OpenAI）；首次出现不必加英文。 |
| test environment | 测试环境 | 不保留英文。 |
| preview | 预览环境 | 指隔离的预览实例；配置键 `preview` 保持代码形式。 |
| finding | 发现 | 指评审提出的问题项；泛指时可译为“评审意见”。 |
| verdict | 结论 | 评审对验收标准给出的判定。 |
| sandbox | 沙箱 | 不保留英文。 |
| spending | 花费 | 指费用统计；不译为“消耗”以免与 token 用量混淆。 |
