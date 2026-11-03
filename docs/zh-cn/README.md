<!-- i18n: source=README.md sha256=5262ab4aca2b -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — 霓虹标志" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>让周五（Friday）早点到来的个人开发流水线。</strong><br>
从任务到草稿 MR：强模型负责规划与评审，低成本模型负责写代码。</p>

<p align="center">Python 3.9+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · 早期版本</p>

[English](../../README.md) · [Русский](../ru/README.md) · **简体中文** · [日本語](../ja/README.md) · [한국어](../ko/README.md) · [Português (Brasil)](../pt-br/README.md) · [Español](../es/README.md) · [Deutsch](../de/README.md) · [Français](../fr/README.md) · [Bahasa Indonesia](../id/README.md)

*本文是英文文档的译文；如与英文版有出入，以英文版为准。*

---

```text
任务 → 计划（强模型） → 计划评审（另一家厂商） → 你的批准
     → 里程碑：沙箱中的执行模型 → 关卡 → 里程碑评审 → UI 的浏览器验收
     → 最终评审 + 盲审 → 草稿 MR
```

你只需参与两次：批准计划，以及验收草稿 MR。在这两个节点之间，流水线不会停下：评审中有争议的发现会随 MR 一并交给你。

## 要点

- **按档位而不是按名称选模型。** 配置文件（profile）中有一份模型目录，其中每个模型都标有厂商和档位（`frontier`、`strong`、`efficient`）。只有 `frontier` 模型审查代码，评审模型的能力不会低于作者，并且尽可能选用不同厂商的模型。昂贵的模型可以标记为每个任务只运行一次。
- **低成本的执行模型，快速的反馈。** 执行模型在无网络的沙箱中自行运行 linter、类型检查和测试，关卡只把失败的检查项反馈给它。
- **进展一目了然。** `friday status` 会显示每个任务所处的环节、使用的模型、代理已沉默多久，以及已经花了多少钱。
- **同时处理多个任务。** 每个任务都有自己的分支和 worktree。机器设置决定同一时间最多可以运行多少个关卡、浏览器，以及同一厂商的多少次运行。

## 快速开始

你需要 Python 3.9+、Git，以及 Claude Code 和/或 Codex CLI。关卡和浏览器验收还需要 Docker。

```sh
git clone git@github.com:avocawo/friday.git && cd friday
mkdir -p profiles
cp examples/models.json examples/project-profile.json profiles/
# in profiles/project-profile.json, set the repository path, the check commands and the gate
./bin/friday doctor --profile profiles/project-profile.json
./bin/friday start TASK-1 --profile profiles/project-profile.json --brief task.md
./bin/friday approve TASK-1
./bin/friday status
```

## 命令

| 命令 | 作用 |
| --- | --- |
| `start KEY --profile P [--brief F]` | 创建 worktree 和计划，并让另一个模型评审该计划 |
| `plan KEY` | 显示计划及尚未解决的发现 |
| `approve KEY [--no-run]` | 批准计划，并在后台启动流水线 |
| `status [KEY] [--json]` | 显示各任务的进展和已花费的金额 |
| `decide KEY name "text" [--reopen M2]` | 记录负责人的决定；只重新打开指定的里程碑 |
| `run KEY` / `resume KEY` / `stop KEY` | 在当前终端中运行、继续、停止 |
| `archive KEY [--force]` | 删除 worktree 和任务卡；分支保留 |
| `journal KEY` | 更新任务日志（Obsidian、jsonl） |
| `doctor --profile P` | 检查机器和配置文件是否就绪 |

有关配置文件、模型和边界的详细说明，请参阅[指南](guide.md)。关于流水线为何这样设计，请参阅[试点分析](../process-redesign.md)（俄文）和 [ADR](../adr/)（俄文）。

## 开发

```sh
docker build -f Dockerfile.test -t friday-test:local .
./bin/test-offline
```

测试使用临时数据和假代理，在无网络的容器中运行。

[指南](guide.md) · [术语表](GLOSSARY.md) · [贡献指南](../../CONTRIBUTING.md) · [许可证](../../LICENSE)
