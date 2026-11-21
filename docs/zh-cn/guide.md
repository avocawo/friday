<!-- i18n: source=docs/guide.md sha256=c8ffd811b55e -->
# Friday 指南

[English](../guide.md) · [Русский](../ru/guide.md) · **简体中文** · [日本語](../ja/guide.md) · [한국어](../ko/guide.md) · [Português (Brasil)](../pt-br/guide.md) · [Español](../es/guide.md) · [Deutsch](../de/guide.md) · [Français](../fr/guide.md) · [Bahasa Indonesia](../id/guide.md)

*本文是英文文档的译文；如与英文版有出入，以英文版为准。*

## 安装

1. 克隆 Friday，并把 `bin` 加入 `PATH`，或者设置 `FRIDAY_BIN`。
2. 安装代理所需的 CLI：Anthropic 和 Z.ai 的模型使用 Claude Code（`claude`），OpenAI 的模型使用 Codex CLI（`codex`）。按常规方式登录它们。
3. 关卡和浏览器验收需要安装 Docker。浏览器验收还需要开启调试端口 9333 的 Chrome，并且该浏览器中要有已登录测试环境的会话。
4. 状态保存在 `~/.local/share/friday`（可用 `FRIDAY_HOME` 覆盖）。配置文件和密钥不会进入仓库：`profiles/` 目录已在 `.gitignore` 中。

### Codex 和 Claude Code 的插件

```sh
codex plugin marketplace add "$PWD" && codex plugin add friday@friday
claude plugin marketplace add "$PWD" && claude plugin install friday@friday --scope user
```

该插件教会代理读取 `status` 和 `plan`。启动任务、批准任务和做出决定则留给你自己。

## 任务如何运行

1. **`start`** 基于最新的 `origin/<base_branch>` 创建分支 `<repository>/KEY`（例如 `octis/31`）和 worktree，锁定角色分配，并编写计划：由 2–5 个阶段组成的里程碑、涉及的文件、规模估算、代码和浏览器验收标准，以及供执行模型使用的项目规则摘要。计划先经过一次不使用模型的检查（规模、文件、顺序、验收标准）。然后由另一家厂商的模型对其评审，规划模型对这些发现只回应一次：接受每一条，或者给出理由予以驳回。
   使用 `"toolset": {"plan": "project"}` 时，由项目自己的流程编写计划（[ADR 0015](../adr/0015-project-toolset.md)）：Friday 在 worktree 中启动一个普通的 Claude Code 会话，使用项目的设置、hook、skill、agent 和 MCP，并把 `project.plan_command` 连同任务描述传给它。该会话编写项目的计划文件（`project.plan_file`），并按 Friday 的格式把同一份计划的摘要返回给 Friday；Friday 不使用模型检查该摘要，也不再让另一家厂商评审，因为项目的流程自带评审。会话以 `auto` 权限模式运行；它的结果是进程退出前的最后一条回答，因为项目的流程可能会等待后台评审。
2. **`plan`** 显示计划、待解答的问题、被驳回的发现，以及与其他活跃任务之间的文件重叠。
3. **`approve`** 批准计划，并在后台启动流水线（`--no-run` 只批准；之后用 `run` 运行）。
   使用 `"toolset": {"execute": "project"}` 时，Friday 运行项目自己的流程，而不是按里程碑执行（[ADR 0015](../adr/0015-project-toolset.md)）：使用 `project.execute_command`，在 `develop` 角色的模型上运行一个 Claude Code 会话。会话为每个阶段提交一次，提交标题以计划摘要中的阶段 id 开头（`M1.1: …`），并且不发布任何内容。提问会结束会话：任务停止，问题显示在 `status` 中，用 `decide` 给出的答案会在后台继续同一个会话；`resume` 不带答案，效果相同。会话会收到使它停下的原因。如果会话在没有提问的情况下结束，而环境使任务停止（关卡镜像、测试环境的登录），`resume` 不会唤醒会话，只会重复它之后的检查。会话结束后，Friday 会检查工作树是干净的，每个阶段都有一个在 `limits` 之内的提交（对其提交使用 `git diff --numstat`），运行关卡，并对带界面的里程碑运行浏览器验收，然后照常进行最终检查并创建 MR；此模式下的最终评审会先读取仓库根目录的 `AGENTS.md`（或 `CLAUDE.md`），并按项目文档的各章节检查 diff，而不仅仅是按计划中的规则。任何不一致都会变成交给负责人的问题。
4. **里程碑。** 执行模型每个里程碑使用一个会话，并为每个阶段提交一次。关卡在该提交上运行。如果失败，执行模型只会收到日志中失败的部分；如果某个部分超过上限，则保留它的标题和带上下文的错误行，其中 TAP 格式的失败测试（`not ok …` 及其诊断信息）排在最前。里程碑评审会结合关卡结果查看本里程碑的 diff。执行模型修复或反驳阻塞性的发现；有争议的发现会交给你。带界面的里程碑会在隔离的预览环境中经过浏览器验收。连续两次失败后，如果配置文件设置了 `executor_tiers` 这一限制，Friday 会在其范围内提高执行模型的档位；替换者会开启新会话。
5. **最终阶段。** `final_review_roles` 中的角色检查整个 diff：默认情况下，最终评审和盲审并行进行；配置文件也可以只保留最终评审。阻塞性的发现有一轮修复机会。
6. 如果配置文件设置了 `delivery`，则会创建**草稿 MR**。否则，其文本保存在任务目录的 `merge-request.md` 中。

流水线会在以下情况停止：关卡从未通过、执行模型什么都没做、环境不可用、规则允许使用的模型已用尽，或者配置的预算已耗尽。并行改动发生重叠、保留下来而未集成的分支，以及最终评审的输入发生漂移，也需要先处理，任务才能继续。评审中的分歧不会让它停下。没有任何检查覆盖的里程碑（没有关卡、执行模型的检查没有运行、没有浏览器验收）会以“已完成，未验证”结案，并在状态、日志和 MR 中写明原因（[ADR 0009](../adr/0009-unverified-is-not-done.md)）；如果执行模型的检查无法运行且没有关卡，则该里程碑被阻塞。

`decide KEY name "text" --reopen M2` 会记录你的决定。在 Friday 的计划等待批准期间，`decide` 还会修订计划（[ADR 0016](../adr/0016-decision-revises-the-plan.md)）：规划模型在新会话中重写 `--reopen` 中指定的里程碑，其余里程碑保持原样；不带 `--reopen` 时，由它自行选择受影响的里程碑。另一个模型只评审有改动的里程碑。`decide` 会打印计划中哪些里程碑被修订、哪些被重新打开以再次运行、哪些只是在上下文中加入了该决定；`friday plan KEY --diff` 显示与上一次修订的差异。如果修订失败，计划保持原样并等待批准，决定已被记录：重复同一条 `decide` 即可。在项目流程的计划等待批准期间，`decide` 会带着你的答案继续其会话，并显示计划的新修订。每一次这样的 `decide` 都是对会话的一次独立继续，因此请在一段文字中回答计划的所有问题。同样，`decide` 也会回答项目流程中已停止的执行会话提出的问题。只有指定的里程碑以及依赖它们的里程碑会被重新打开。不带 `--reopen` 时，后续的里程碑会参考该决定。`resume` 会继续已停止或已失败的任务。`stop` 会停止正在运行的任务：代理连同其子进程一起结束。

任务的原始描述会完整保存，并传给执行模型和各类评审，因此精确的哈希和 URL 不会在计划压缩事实时丢失。对于没有保存描述的旧任务卡，请使用 `resume KEY --brief task.md`。只有当文本与最初规划时的指纹一致，描述才会被还原；其他文本需要重新制定计划。批准状态、阶段和预算计数器都不会重置。

失败的里程碑继续时，已经提交的内容保持原有的评审边界；不需要额外提交一个空提交。执行模型会拿到上一次关卡的失败信息，其中包含 Python traceback 的完整正文，受诊断输出上限的限制。

## 配置文件

配置文件是一个 JSON 文件。`extends` 键用于包含另一个配置文件（例如共享的模型目录）；路径相对于该配置文件。

| 键 | 用途 |
| --- | --- |
| `name`, `repository` | 项目名称及其仓库的路径 |
| `base_branch`, `worktree_root`, `branch_template`, `worktree_layout` | 从哪里创建任务分支以及把它放在哪里；默认为 `<repository>/{key}`，显式的 `branch_template` 只能包含一个 `{key}`，布局为 `key` 或 `branch` |
| `models`, `roles` | 模型目录和角色偏好（见下文） |
| `engineering_policy` | 给规划模型、执行模型、里程碑评审、最终评审和修复的规则 |
| `project_lessons` | 评审一再发现的项目陷阱：一份简短检查项的列表。执行模型、里程碑评审、最终评审和修复都会据此检查改动 |
| `rules_docs` | 包含项目规则的文档。Friday 会为规划模型和计划评审模型把它们汇总成一份摘要（默认为 `AGENTS.md`、`CLAUDE.md`、`docs/*.md`）。摘要按内容哈希存储，只要文档不变，各任务就会复用它；规划模型不会完整阅读这些文档。模式从仓库根目录展开，`**` 会进入子目录（`docs/**/*.md`、`src/**/README.md`）；只有找到的文件才会进入指纹。执行模型、里程碑评审、最终评审和修复会拿到计划选定的规则、摘要中的文档地图，以及任务的基线修订，用于查找先例；执行模型用 `bin/friday-precedent <base> <pattern> [<path>…]` 搜索（这是对 `git grep` 的封装，不放行任何选项） |
| `repo_map`, `repo_map_budget`, `repo_map_mcp` | 在规划模型和计划评审模型的提示词中加入仓库地图，使它们不必通过工具调用来探索目录结构。`none`（默认）：没有地图。`builtin`：受版本控制的文件及其行数。`graphify`：另外包含每个文件的符号以及有多少文件导入它，数据取自 [graphify](https://github.com/Graphify-Labs/graphify) 图（`uv tool install 'graphifyy[mcp]==0.9.100'`；它只解析代码，不使用模型；图保存在 `FRIDAY_HOME/maps` 中，而不是仓库里）。在 CS-2 上的一次测量中，地图并没有让规划变得更便宜（无地图 $6.19，`builtin` $7.08，`graphify` $7.97，各跑一次），因此默认关闭。地图按提交缓存。如果它超过 `repo_map_budget` 个字符（60000），会先通过去掉符号来缩短，再通过折叠目录来缩短。`repo_map_mcp: true`（仅与 `graphify` 配合）会让这两个角色都能查询图：`query_graph`、`get_node`、`get_neighbors`、`shortest_path`；尚未在 Codex 上实测验证。计划评审模型会收到计划中提到的文件的当前文本（最多 40000 个字符） |
| `approval` | `owner`（默认）：计划等待 `approve`。`auto`：计划在评审后自动批准，流水线立即启动；日志会记录计划是自动批准的 |
| `limits` | `stage_files` 和 `stage_lines`：一个阶段的上限（10 和 500）；可选的 `task_milestones` 和 `task_stages` 会在计划运行之前限制整个计划 |
| `toolset`, `project` | 由谁运行某个环节：`{"plan": "project", "execute": "friday"}` 表示由项目的流程编写计划，由 Friday 来运行它（两个环节默认都是 `friday`）。`project`：带 `{statement}` 的 `plan_command` 是项目的规划命令，例如 `"/plan-stages {statement}"`；`plan_file` 是 worktree 中的计划文件；带 `{stages}` 的 `execute_command` 和 `stage_argument` 用于执行环节；`session_env` 列出会话在通常环境变量之外获得的环境变量（例如用于 `git fetch` 的 `SSH_AUTH_SOCK`）；`disallowed_tools` 是对会话关闭的工具（例如负责人的浏览器：验收由 Friday 的 `browser` 角色完成）；`timeouts` 是 `idle`、`tool`、`total`，单位为秒（[ADR 0015](../adr/0015-project-toolset.md)） |
| `kind` | `delivery`（默认）或 `analysis`。分析任务在项目会话中运行 `project.analysis_command`（带 `{key}`），通过 `decide` 向负责人提问，并在 `project.analysis_check`（含 `{key}` 和 `{worktree}` 的参数数组）通过后完成；它没有里程碑，也没有 MR。需要 `"toolset": {"plan": "project"}`；不使用 `plan_command` 和 `plan_file`；`--brief` 可代替尚不存在的工单（[ADR 0017](../adr/0017-analysis-kind.md)） |
| `worktree_setup` | Friday 在规划前于新 worktree 中运行的命令（依赖安装等）：参数数组，可使用替换变量 `{repository}` 和 `{worktree}`；必须可以重复执行。macOS 上 npm 的示例：`["sh", "-c", "[ -e node_modules ] \|\| cp -cR {repository}/node_modules node_modules"]` |
| `executor_commands` | 执行模型可以自行运行的命令，必须是精确的形式：`"python3 -m pytest"`，而不是 `python` |
| `executor_deny_read` | 执行模型的 shell 无法读取的额外路径 |
| `executor_tiers` | 所有写代码的代理允许使用的档位，例如 `["strong", "efficient"]`。不设置该字段则允许所有档位。复杂度、升级、配额和最终修复都遵守这一限制 |
| `milestone_rounds` | 一个里程碑可获得多少轮执行（4） |
| `max_parallel_milestones` | 带 `parallel=true`、彼此独立且已就绪的里程碑中，同时运行的数量（2）；`1` 表示依次运行 |
| `budget` | 可选的正整数 `max_model_runs` 和 `max_milestone_runs`：每个任务和每个里程碑允许的所有角色运行次数上限。预留是原子的；失败和未完成的尝试也计入，`resume` 和重新打开都不会重置计数器。耗尽时任务以 `budget_exhausted` 停止；预算只能在配置文件中显式修改。它限制的是运行次数，而不是费用 |
| `final_review_roles` | 默认为 `["final-review", "blind-code"]`；`["final-review"]` 只保留一次最终检查。已完成的结果各自独立保存，只有在源码、计划、决定、设置和该角色的输入都没有变化时才会复用 |
| `gate`, `gate_image`, `gate_mode`, `gate_*` | 在 Docker 中运行的检查命令；`git-diff` 与任务基线进行比较。配置文件把构建关卡镜像的方法描述为一个步骤（`steps`），`gate_image_step` 指明该步骤。如果镜像中的 `gate_lockfile` 与任务中的不同，任务会停止：`status` 显示“Owner step needed”及该步骤的命令；步骤完成后，`resume` 会重复检查，并且不会唤醒项目流程的执行会话 |
| `environment_errors` | 表示环境故障而非代码故障的日志行模式 |
| `browser`, `preview`, `behavior_test_policy` | 测试环境、预览环境和浏览器验收的规则。预览后端包括 `storybook`（来自 `stories` 的展示库，其中 `mount` 处挂载着组件库的快照，[ADR 0008](../adr/0008-ui-kit-preview-and-design-check.md)）。对于 `storybook`，所有与项目相关的内容都在配置文件中：`command`（启动展示库的参数数组；`{mount}` 是组件库的路径）、`port`、`env`、`tmpfs`（可写入的容器内绝对路径）、`links`（从快照指向镜像的链接，例如 `{"node_modules": "/app/node_modules"}`）；镜像是 `stories.image` |
| `stories` | 任务的第二个仓库，即容纳组件库 worktree 的组件展示库（Storybook）：`repository`、`base_branch`、`worktree_root`、`mount`（必填：组件库在展示库中的路径）、`setup`（在新 worktree 中运行的命令；`{repository}`、`{worktree}`、`{kit}`）、`commands`（执行模型的检查）、`image`（预览镜像）、`themes`（浏览器验收会在每个主题中打开该 story 并比较组件的各个状态）以及 `theme_query`（与 `themes` 一起必填：带 `{theme}` 的主题 URL 参数）。展示库的文件在计划中写作 `stories:src/stories/X.stories.ts`；结果是两个仓库中各有一个任务分支 |
| `steps` | Friday 自己不了解的项目步骤；只由负责人运行（`friday step NAME [KEY]`，不带名称则列出各步骤及其状态）（[ADR 0011](../adr/0011-profile-steps.md)）：`{"<name>": {"run": [...], "scope": "task" \| "profile", ...}}`。`run` 是在主机上运行的参数数组；任务步骤可用 `{key}`、`{worktree}`、`{branch}`、`{base}`、`{head}`、`{showcase}`，任何步骤都可用 `{profile_dir}`、`{repository}`、`{base_branch}`、`{stories_repository}`、`{secret.<name>}`。`description`、`env`（不含密钥值）、`cwd`。`lock` 是步骤持续期间的共享资源：路径表示共用仓库的锁，名称表示具名锁。`commit: {"paths": [...], "message": "{key}: ..."}` 让 Friday 提交这些路径；如果在它提交之后，分支在这些路径之外发生变化，该步骤就会过期。`required`（任务步骤）：在该步骤未完成或已过期期间，`status`、日志和 MR 文本会连同它的命令一起提醒。`check`（配置文件步骤）是给 `doctor` 用的就绪检查命令：0 表示就绪，2 表示已过期（!），其他值表示缺失（✗）。`secrets: {"<name>": "<path>"}` 是命令按路径获得的文件；Friday 不会读取它们。把复杂的流程放进配置文件旁边的脚本里 |
| `design` | 用于 UI 任务的设计工具（[ADR 0007](../adr/0007-design-tools.md)）。`figma: {"url": "http://127.0.0.1:3845/mcp"}` 是 Figma 桌面版的本地 Dev Mode MCP 服务器；`tools` 用于收窄只读工具（`get_metadata`、`get_design_context`、`get_screenshot`、`get_variable_defs`），不接受写入工具。当工单中有 `figma.com/design/…?node-id=…` 链接时，`start` 会检查该节点是否已在活动的 Figma 标签页中打开，规划模型和计划评审模型会读取该画板，并把可度量的规格写入计划。执行模型和各类评审在提示词中获得该规格，但不能访问 Figma。Friday 会把画板截图保存在任务目录中；浏览器验收会把预览环境与它们对比，并测量样式（`computed_style`）：与规格中数值的差异会阻塞，只凭肉眼看出的差异则记入 MR 的次要发现。如果 Figma 没有响应或找不到该节点，`start` 会停止并说明需要打开什么；`start --no-design` 则不使用设计稿来规划。`impeccable: {"skill_dir": ".../skills/impeccable", "references": {...}}` 指向 /impeccable skill 的 `reference/` 中的文件，Friday 会把它们放进 UI 里程碑的提示词；`references` 必填，它把角色 `develop`、`review`、`final-review` 映射到文件列表（例如 `{"develop": ["polish.md"]}`） |
| `tracker` | Yandex Tracker（API 或 MCP），用于读取任务描述 |
| `journal` | 任务日志写到哪里：`[{"kind": "markdown", "dir": "...", "page": "{key}/Friday.md"}, {"kind": "jsonl", "dir": "..."}]`；`page` 是任务页面在 `dir` 内的路径（默认为 `{key}.md`），例如指向 Obsidian 中该任务的目录。`journal_dir` 和 `journal_page` 是只有一份 markdown 日志时的简写；配置文件中设置的（或继承的）`journal` 优先于它们。Friday 不会改动不是它自己写的页面（front matter 中没有 `friday_generated`）：它会打印已跳过该页面，并写入其余部分 |
| `delivery` | `{"transport": "github" \| "gitlab" \| "none", "target_branch": "..."}`，以及键 `github.repository` 或 `gitlab.base_url` + `gitlab.project` |

仓库名称取自 `origin` 的 URL（HTTPS、SSH 或本地路径），不含 `.git`；没有 `origin` 时，取自磁盘上主仓库的名称，从关联的 worktree 工作时也是如此。`A–Z`、`a–z`、`0–9`、`_` 和 `-` 之外的字符会变成 `-`，开头和结尾的 `-` 会被删除。名称为空时，需要显式的 `branch_template`。任务 key 会完整保留：`31` 得到 `octis/31`，`OCTIS-031` 得到 `octis/OCTIS-031`。任务卡上已经记录的分支会在任务继续时保留；新的设置只对新任务生效。关联的展示库使用任务主仓库的名称。

### 模型和角色

```json
"models": {
  "fable": {"model": "claude-fable-5-2", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "strength": 1, "scarce": true, "effort": "high"},
  "opus-high": {"model": "claude-opus-5-5", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "effort": "high"},
  "glm-high": {"model": "glm-5.3", "vendor": "zai", "driver": "claude", "tier": "strong", "effort": "high",
               "token_file": "~/.config/friday/zai.env", "token_key": "ZAI_API_KEY"}
},
"roles": {"plan": ["fable"], "develop": ["glm-high"], "review": ["opus-high"]}
```

- `driver`：运行模型的程序，`claude` 或 `codex`。Z.ai 通过 Claude Code 运行；令牌从只有所有者可访问的文件中读取。
- `tier`：`frontier`、`strong` 或 `efficient`；`strength` 用于在同一档位内给模型排序。
- `effort`：推理深度（`low` … `max`）；同一个模型可以用不同的 effort 描述多次。
- `scarce`：昂贵的模型；如果规则允许任务不使用它，则每个任务只用一次。
- `price`：按类型（`input`、`output`、`cache_read`、`cache_write`）给出的每百万 token 价格，在厂商不报告价格时用于估算花费。

角色：`plan`、`plan-review`、`develop`、`review`、`final-review`、`blind-code`、`browser`。`roles` 中的列表只是偏好：如果列表中没有合适的模型，Friday 会从目录中选取另一个。硬性规则：只有 `frontier` 模型审查代码，评审模型不弱于工作成果的作者，并且盲审不由代码的作者来做。软性规则，按顺序为：不与作者使用同一个模型；昂贵的模型只用一次；使用不同的厂商；盲审不由计划的作者来做。只有在别无他法时才可以打破软性规则，并且违反情况会记录在任务历史中。

角色分配的示例见 [examples/models.json](../../examples/models.json)。

使用 `executor_tiers` 时，复杂的里程碑会使用允许的最高档位，配额不足时的替换者会在该限制内保持所需的档位。旧的角色分配中不被允许的执行模型会被换成允许的模型；被禁止的模型或与目录不符的模型的写入会被拒绝。档位和名称来自配置文件的目录；Friday 不会从模型名称推断它们。

并行的里程碑有各自的 worktree，并按计划顺序集成。Friday 会在启动前检查声明的路径是否重叠，并在集成前检查实际改动是否重叠。无法集成的工作会保留在它的分支和 worktree 中；任务继续之前，`parallel_recovery_required` 会列出它们。审阅完保留下来的工作后，由负责人完成迁移，或另行保留需要的提交，然后用普通的 Git 命令删除已审阅的并行 worktree 和分支。Friday 不会为了重试而自动删除这些结果。

已保存的最终评审绑定到干净的 worktree、两个仓库的提交、计划、决定、模型设置，以及该角色输入的精确文本。检查期间输入发生变化会返回 `stale_evidence`。当第二个角色失败时，已完成的第一个会被保留；下一次 `resume` 只会针对相同的输入运行缺少的检查。提高 `budget` 和修改 `max_parallel_milestones` 不会使已完成的评审失效：这些设置控制的是准入和调度，而不是检查的内容。

## 日志

任务卡是唯一的事实来源。日志是它的呈现，并在重要事件之后自动更新：计划就绪并获批、里程碑完成或失败、负责人的决定、最终评审、MR、停止。如需手动更新，请运行 `friday journal KEY`。

时间是各次运行时长之和，包括并行运行，而不是日历时间。对于提供方没有给出时长的新运行，由运行器自己测量。运行器启动之前的中断会记录为零执行时间；新的未测量运行的时长保持未知。旧的关联事件会给出一个明确标注的估算，其中可能包含等待名额的时间。丢失且没有测量的运行，其时长未知。未知的价格也会按角色显示；它不会变成零花费。

- `markdown`：一个 `KEY.md` 页面，以及一个列出所有任务的共享总览页。对于 Obsidian，请设置 vault 目录：页面带有 front matter（key、状态、花费、标签 `friday`），任务之间通过 wiki 链接相互关联。任务页面包含带有假设和未解决发现的计划、带有执行模型摘要和提交的里程碑、有争议的发现、各项决定、按角色统计的花费，以及时间线。
- `jsonl`：任务的所有事件，用于在电子表格或仪表盘中分析。

模板是 Markdown，可使用替换变量 `{{key}}`、`{{state}}`、`{{status}}`、`{{plan}}`、`{{milestones}}`、`{{disputes}}`、`{{decisions}}`、`{{costs}}`、`{{timeline}}`、`{{notes}}`。Friday 依次取用目标的 `template` 字段中的模板、项目仓库中的 `.friday/task.md`，最后是内置模板（[friday/templates](../../friday/templates)）。`<!-- friday:notes -->` 与 `<!-- /friday:notes -->` 之间的文字归你所有：每次更新时都会保留。Friday 不会覆盖不是它自己写的页面。

## 机器设置

`FRIDAY_HOME/machine.json` 设置所有任务共用的资源：

```json
{"slots": {"gate": 2, "browser": 2, "vendor:zai": 2}, "ports": [5200, 5399]}
```

默认情况下，同一时间运行一个关卡和两个浏览器，对厂商不设限制。厂商的配额会被自动跟踪：某次运行触及限额后，会记录配额恢复的时间，在此之前选择模型时会跳过该厂商。

### 任务队列：`friday serve`

`friday serve` 是一个长期运行的进程，每台机器有一个已批准任务的队列（[ADR 0014](../adr/0014-work-pool.md)）。它运行期间，`approve`、`resume` 以及用 `decide` 回答执行会话的答案不会自己启动任务，而是把任务放入队列；没有它时，任务照旧作为单独的后台进程运行，`resume` 在终端中运行。

- 同一时间最多运行 `max_workers` 个任务（`machine.json`，默认为 2）；在队列之外启动的任务也计入。
- 队列按计划获批的时间排序；用 `decide` 之后继续的任务排在最前。`friday status` 显示队列和服务器的状态，`friday stop` 会把任务从队列中移除。
- 状态为“运行中”但进程已经死亡（休眠、重启）的任务，服务器会以 `resume` 的方式把它放回队列。
- 当任务因问题或负责人需要完成的步骤而停止、失败或完成时，会收到系统通知（macOS 上用 `osascript`，Linux 上用 `notify-send`）。

对服务器而言，一个任务就是一个作业：里程碑不会被拆分成多个作业，目前也没有针对单个任务的上限。

`friday service print|install|uninstall` 会把服务器安装为用户服务：launchd 代理（`~/Library/LaunchAgents/local.friday.serve.plist`）或 systemd 单元。服务启动时环境为空，所以它的文件会自己写明一切：`PATH` 由 `claude`、`codex`、`docker`、`git`（以及存在时的 `glab`、`gh`、`uvx`、`node`、`npx`）所在的目录组成，还有 `FRIDAY_HOME`、`HOME`。`friday service check --profile P` 会在恰好这样的环境中运行 `friday doctor`，因此在安装之前你就能看到服务找不到什么。服务能否找到 Claude 订阅的登录状态，只有在它下面运行的第一个任务才能说明。服务使用 `friday config language` 保存的语言。

## 边界

- **执行模型的沙箱。** 执行模型的 shell（Claude Code）在沙箱中运行：没有网络，不能读取 `~/.ssh`、`~/.aws`、`~/.config` 及类似目录，只能写入 worktree 和临时目录，也不能在沙箱之外运行。不属于 `executor_commands`、git 命令（`add`、`commit`、`status`、`diff`、`log`、`restore`）和 `bin/friday-precedent` 的命令会被拒绝。Codex 在它自己的沙箱中运行，边界相同。
- **Codex 中的 Git。** Friday 会把已安装的 Git 和一份单独的 Git 元数据副本放入任务的运行环境。全局 Git 配置和用户的 shell 配置文件不会被加载；源仓库的服务数据不会暴露给代理。执行模型用普通的 Git 命令保存阶段。在它的进程结束之后，Friday 通过经过验证的 bundle 取回这些提交，如果源分支的 HEAD 和索引没有变化，就把它们放回源分支。出现状态冲突时，bundle 会以 `git-recovery.bundle` 留在运行日志中；不会自动覆盖任何内容。评审模型只能以只读方式获得 Git 元数据。`doctor` 会在不调用模型的情况下，检查在关联 worktree 中提交三次的真实场景，并检查禁止读取密钥以及禁止评审模型写入的限制是否生效。
- **评审模型**只读。盲审在空目录中运行。
- **关卡**在没有网络的容器中运行，使用以只读方式挂载的提交快照。
- **浏览器**只会获得 Friday 为单个预览环境提供的工具（包括 `hover` 和 `computed_style`，后者用固定的脚本读取样式，不使用 `evaluate_script`）；禁止绕过授权，也禁止读取已授权请求的请求头。
- **Figma** 只由规划模型和计划评审模型读取，并且只使用本地 Figma 桌面版服务器的只读工具。/impeccable skill 不会在这些角色内部运行：Friday 会把它的文本放进提示词。
- **发布。** Friday 只推送自己任务的分支，并且只创建草稿 MR/PR。它不会覆盖他人的分支、不会合并，也不会部署。
- **尚未在真实环境验证：** 浏览器验收以及向 GitLab 发布，目前已通过使用假对象的测试验证。Codex 环境中的 Git 还通过一个不调用模型的真实本地实验环境做了检查；模型运行某个角色的情况则在每个任务上单独检查。
