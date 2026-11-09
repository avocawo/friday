<!-- i18n: source=docs/guide.md sha256=9198a3cbcbff -->
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

1. **`start`** 基于最新的 `origin/<base_branch>` 创建分支 `friday/KEY` 和 worktree，锁定角色分配，并编写计划：由 2–5 个阶段组成的里程碑、涉及的文件、规模估算、代码和浏览器验收标准，以及供执行模型使用的项目规则摘要。计划先经过一次不使用模型的检查（规模、文件、顺序、验收标准）。然后由另一家厂商的模型对其评审，规划模型对这些发现只回应一次：接受每一条，或者给出理由予以驳回。
2. **`plan`** 显示计划、待解答的问题、被驳回的发现，以及与其他活跃任务之间的文件重叠。
3. **`approve`** 批准计划，并在后台启动流水线（`--no-run` 只批准；之后用 `run` 运行）。
4. **里程碑。** 执行模型每个里程碑使用一个会话，并为每个阶段提交一次。关卡在该提交上运行。如果失败，执行模型只会收到日志中失败的部分。里程碑评审会结合关卡结果查看本里程碑的 diff。执行模型修复或反驳阻塞性的发现；有争议的发现会交给你。带界面的里程碑会在隔离的预览环境中经过浏览器验收。连续两次失败后，更高档位的模型会在新会话中接手该里程碑。
5. **最终阶段。** 对整个 diff 的最终评审和盲审并行进行。阻塞性的发现有一轮修复机会。
6. 如果配置文件设置了 `delivery`，则会创建**草稿 MR**。否则，其文本保存在任务目录的 `merge-request.md` 中。

只有在以下情况流水线才会停止：关卡从未通过、执行模型什么都没做、环境不可用，或者规则允许使用的模型已用尽。评审中的分歧不会让它停下。

`decide KEY name "text" --reopen M2` 会记录你的决定。只有指定的里程碑以及依赖它们的里程碑会被重新打开。不带 `--reopen` 时，后续的里程碑会参考该决定。`resume` 会继续已停止或已失败的任务。`stop` 会停止正在运行的任务：代理连同其子进程一起结束。

要在批准前退回计划，先用 `decide` 记录要改什么，再运行 `replan KEY`。规划模型会在新会话中修订这份等待中的计划，修订理由是该计划之后你记录的决定。修订后的计划会再次经过检查和另一家厂商模型的评审，然后等待你批准，即使设置了 `approval: auto` 也是如此。任务保留其卡片、历史和费用。`decide` 本身从不运行模型，所以你可以记录多个决定后只重新规划一次。如果重新规划失败，之前的计划仍在等待批准。对于在 Friday 开始保存任务描述之前启动的任务，可用 `--brief` 再次提供任务描述。

## 配置文件

配置文件是一个 JSON 文件。`extends` 键用于包含另一个配置文件（例如共享的模型目录）；路径相对于该配置文件。

| 键 | 用途 |
| --- | --- |
| `name`, `repository` | 项目名称及其仓库的路径 |
| `base_branch`, `worktree_root`, `branch_template`, `worktree_layout` | 从哪里创建任务分支以及把它放在哪里；默认模板为 `friday/{key}`，布局为 `key` 或 `branch` |
| `models`, `roles` | 模型目录和角色偏好（见下文） |
| `engineering_policy` | 给规划模型和各类评审的规则 |
| `rules_docs` | 包含项目规则的文档。Friday 会为规划模型和计划评审模型把它们汇总成一份摘要（默认为 `AGENTS.md`、`CLAUDE.md`、`docs/*.md`）。摘要按内容哈希存储，只要文档不变，各任务就会复用它；规划模型不会完整阅读这些文档 |
| `repo_map`, `repo_map_budget`, `repo_map_mcp` | 在规划模型和计划评审模型的提示词中加入仓库地图，使它们不必通过工具调用来探索目录结构。`none`（默认）：没有地图。`builtin`：受版本控制的文件及其行数。`graphify`：另外包含每个文件的符号以及有多少文件导入它，数据取自 [graphify](https://github.com/Graphify-Labs/graphify) 图（`uv tool install 'graphifyy[mcp]==0.9.100'`；它只解析代码，不使用模型；图保存在 `FRIDAY_HOME/maps` 中，而不是仓库里）。在 CS-2 上的一次测量中，地图并没有让规划变得更便宜（无地图 $6.19，`builtin` $7.08，`graphify` $7.97，各跑一次），因此默认关闭。地图按提交缓存。如果它超过 `repo_map_budget` 个字符（60000），会先通过去掉符号来缩短，再通过折叠目录来缩短。`repo_map_mcp: true`（仅与 `graphify` 配合）会让这两个角色都能查询图：`query_graph`、`get_node`、`get_neighbors`、`shortest_path`；尚未在 Codex 上实测验证。计划评审模型会收到计划中提到的文件的当前文本（最多 40000 个字符） |
| `approval` | `owner`（默认）：计划等待 `approve`。`auto`：计划在评审后自动批准，流水线立即启动；日志会记录计划是自动批准的 |
| `limits` | `stage_files` 和 `stage_lines`：一个阶段的上限（10 和 500） |
| `worktree_setup` | Friday 在规划前于新 worktree 中运行的命令（依赖安装等）：参数数组，可使用替换变量 `{repository}` 和 `{worktree}`；必须可以重复执行。macOS 上 npm 的示例：`["sh", "-c", "[ -e node_modules ] \|\| cp -cR {repository}/node_modules node_modules"]` |
| `executor_commands` | 执行模型可以自行运行的命令，必须是精确的形式：`"python3 -m pytest"`，而不是 `python` |
| `executor_deny_read` | 执行模型的 shell 无法读取的额外路径 |
| `milestone_rounds` | 一个里程碑可获得多少轮执行（4） |
| `gate`, `gate_image`, `gate_mode`, `gate_*` | 在 Docker 中运行的检查命令；`git-diff` 与任务基线进行比较 |
| `environment_errors` | 表示环境故障而非代码故障的日志行模式 |
| `browser`, `preview`, `behavior_test_policy` | 测试环境、预览环境和浏览器验收的规则 |
| `tracker` | Yandex Tracker（API 或 MCP），用于读取任务描述 |
| `journal` | 任务日志写到哪里：`[{"kind": "markdown", "dir": "..."}, {"kind": "jsonl", "dir": "..."}]`；`journal_dir` 是只有一份 markdown 日志时的简写 |
| `delivery` | `{"transport": "github" \| "gitlab" \| "none", "target_branch": "..."}`，以及键 `github.repository` 或 `gitlab.base_url` + `gitlab.project` |

### 模型和角色

```json
"models": {
  "fable": {"model": "claude-fable-5-1", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "strength": 1, "scarce": true, "effort": "high"},
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

## 日志

任务卡是唯一的事实来源。日志是它的呈现，并在重要事件之后自动更新：计划就绪并获批、里程碑完成或失败、负责人的决定、最终评审、MR、停止。如需手动更新，请运行 `friday journal KEY`。

- `markdown`：一个 `KEY.md` 页面，以及一个列出所有任务的共享总览页。对于 Obsidian，请设置 vault 目录：页面带有 front matter（key、状态、花费、标签 `friday`），任务之间通过 wiki 链接相互关联。任务页面包含带有假设和未解决发现的计划、带有执行模型摘要和提交的里程碑、有争议的发现、各项决定、按角色统计的花费，以及时间线。
- `jsonl`：任务的所有事件，用于在电子表格或仪表盘中分析。

模板是 Markdown，可使用替换变量 `{{key}}`、`{{state}}`、`{{status}}`、`{{plan}}`、`{{milestones}}`、`{{disputes}}`、`{{decisions}}`、`{{costs}}`、`{{timeline}}`、`{{notes}}`。Friday 依次取用目标的 `template` 字段中的模板、项目仓库中的 `.friday/task.md`，最后是内置模板（[friday/templates](../../friday/templates)）。`<!-- friday:notes -->` 与 `<!-- /friday:notes -->` 之间的文字归你所有：每次更新时都会保留。Friday 不会覆盖不是它自己写的页面。

## 机器设置

`FRIDAY_HOME/machine.json` 设置所有任务共用的资源：

```json
{"slots": {"gate": 2, "browser": 2, "vendor:zai": 2}, "ports": [5200, 5399]}
```

默认情况下，同一时间运行一个关卡和两个浏览器，对厂商不设限制。厂商的配额会被自动跟踪：某次运行触及限额后，会记录配额恢复的时间，在此之前选择模型时会跳过该厂商。

## 边界

- **执行模型的沙箱。** 执行模型的 shell（Claude Code）在沙箱中运行：没有网络，不能读取 `~/.ssh`、`~/.aws`、`~/.config` 及类似目录，只能写入 worktree 和临时目录，也不能在沙箱之外运行。不在 `executor_commands` 中且不是 git 命令的命令会被拒绝。Codex 在它自己的沙箱中运行，边界相同。
- **评审模型**只读。盲审在空目录中运行。
- **关卡**在没有网络的容器中运行，使用以只读方式挂载的提交快照。
- **浏览器**只会获得 Friday 为单个预览环境提供的工具；禁止绕过授权，也禁止读取已授权请求的请求头。
- **发布。** Friday 只推送自己任务的分支，并且只创建草稿 MR/PR。它不会覆盖他人的分支、不会合并，也不会部署。
- **尚未在真实环境验证：** Codex 上的角色、浏览器验收以及向 GitLab 发布，目前仅通过使用假对象的测试验证过。
