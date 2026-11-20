<!-- i18n: source=README.md sha256=b45badbf640b -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — 霓虹标志" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>让周五（Friday）早点到来的个人开发流水线。</strong><br>
从任务到草稿 MR：强模型负责规划与评审，低成本模型负责写代码。</p>

<p align="center">Python 3.11+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · 早期版本</p>

[English](../../README.md) · [Русский](../ru/README.md) · **简体中文** · [日本語](../ja/README.md) · [한국어](../ko/README.md) · [Português (Brasil)](../pt-br/README.md) · [Español](../es/README.md) · [Deutsch](../de/README.md) · [Français](../fr/README.md) · [Bahasa Indonesia](../id/README.md)

*本文是英文文档的译文；如与英文版有出入，以英文版为准。*

---

```text
任务 → 计划（强模型） → 计划评审（另一家厂商） → 你的批准
     → 里程碑：沙箱中的执行模型 → 关卡 → 里程碑评审 → UI 的浏览器验收
     → 最终评审 + 盲审 → 草稿 MR
```

你批准计划并验收草稿 MR。流水线会在配置的预算内执行计划；预算耗尽或环境损坏时，任务会交回负责人。评审中有争议的发现会随 MR 一并交给你。

## 要点

- **按档位而不是按名称选模型。** 配置文件（profile）中有一份模型目录，其中每个模型都标有厂商和档位（`frontier`、`strong`、`efficient`）。只有 `frontier` 模型审查代码，评审模型的能力不会低于作者，并且尽可能选用不同厂商的模型。昂贵的模型可以标记为每个任务只运行一次。
- **低成本的执行模型，快速的反馈。** 执行模型在无网络的沙箱中自行运行 linter、类型检查和测试，关卡只把失败的检查项反馈给它。
- **进展一目了然。** `friday status` 会显示每个任务所处的环节、使用的模型、代理已沉默多久，以及已经花了多少钱。
- **同时处理多个任务。** 每个任务都有自己的分支和 worktree。机器设置决定同一时间最多可以运行多少个关卡、浏览器，以及同一厂商的多少次运行。
- **以项目命名的分支。** 默认情况下，`start` 创建 `<repository>/<task key>`，例如 `octis/31`。名称取自 Git 的 `origin`；配置文件可以设置自己的模板。
- **有上限的花费。** 配置文件可以用 `executor_tiers` 排除 frontier 档位的执行模型，并用 `budget` 限制整个计划的规模和模型运行的次数；`resume` 不会重置计数器。最终评审按角色保存，因此在输入不变的情况下恢复时不会再次运行它。
- **并行的里程碑。** 彼此独立且已就绪的里程碑会在各自的 worktree 中运行，默认同时两个；它们共同改动的文件会阻止集成。

## 快速开始

你需要 Python 3.11+、Git 和 Claude Code（Codex CLI 为可选）。关卡和浏览器验收还需要 Docker。

```sh
uv tool install git+https://github.com/avocawo/friday   # or: pipx install git+https://github.com/avocawo/friday
mkdir -p ~/.config/friday/profiles && cd ~/.config/friday/profiles
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/models.json
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/project-profile.json
# in project-profile.json, set your project's repository, worktree_root and executor_commands
export FRIDAY_PROFILE=~/.config/friday/profiles/project-profile.json
friday doctor
friday start TASK-1 --brief task.md
friday approve TASK-1
friday status
```

Friday 支持十种语言：`friday --lang ru …`、`FRIDAY_LANG=ru`，或 `friday config language ru`。代理始终使用英文工作；你读到的内容会被翻译。Node 和 Python 项目的示例配置文件见 [examples/profiles](../../examples/profiles)。

## 命令

| 命令 | 作用 |
| --- | --- |
| `start KEY --profile P [--brief F] [--no-design]` | 创建 worktree 和计划，并让另一个模型评审该计划；配置文件中有 `design` 时，会对照工单中的 Figma 画板检查计划 |
| `plan KEY [--diff]` | 显示计划及尚未解决的发现；`--diff` 显示最近一次修订改动了什么 |
| `approve KEY [--no-run]` | 批准计划，并在后台启动流水线 |
| `status [KEY] [--json]` | 显示各任务的进展和已花费的金额 |
| `decide KEY name "text" [--reopen M2]` | 记录负责人的决定。计划等待批准期间，规划模型会按该决定修订指定的里程碑（不带 `--reopen` 时由它自行选择），并且只重新评审有改动的里程碑（[ADR 0016](../adr/0016-decision-revises-the-plan.md)）；批准之后，只重新打开指定的里程碑 |
| `run KEY` / `resume KEY [--brief F]` / `stop KEY` | 在当前终端中运行、继续、停止；`--brief` 按规划指纹还原旧任务的原始描述 |
| `serve` / `service print\|install\|uninstall\|check` | 本机的任务队列：依次启动任务、把失败的任务放回队列、发送通知；作为 launchd 或 systemd 服务运行（[指南](guide.md#任务队列friday-serve)） |
| `step [NAME] [KEY] [--profile P] [--dry-run]` | 运行配置文件步骤（`steps`）：任务步骤需给出任务 key，配置文件步骤使用 `--profile`；不带名称时列出各步骤及其状态。步骤的具体内容（构建镜像、构建包等）由配置文件描述（[ADR 0011](../adr/0011-profile-steps.md)） |
| `archive KEY [--force]` | 删除 worktree 和任务卡；分支保留 |
| `journal KEY` | 更新任务日志（Obsidian、jsonl） |
| `doctor --profile P` | 检查机器和配置文件是否就绪 |
| `config language L` | 设置 Friday 在这台机器上使用的语言 |

有关配置文件、模型和边界的详细说明，请参阅[指南](guide.md)。关于流水线为何这样设计，请参阅[试点分析](../process-redesign.md)（俄文）和 [ADR](../adr/)（0001–0006 为俄文）。

## 开发

```sh
git clone https://github.com/avocawo/friday.git && cd friday
python3 -m unittest discover -s tests
uvx ruff check .
python3 -m friday.i18n
```

测试使用临时数据和假代理，不需要网络，也不需要 Docker。代码的组织方式见 [docs/architecture.md](../architecture.md)；如何提出修改见 [CONTRIBUTING.md](../../CONTRIBUTING.md)。

[指南](guide.md) · [术语表](GLOSSARY.md) · [贡献指南](../../CONTRIBUTING.md) · [许可证](../../LICENSE)
