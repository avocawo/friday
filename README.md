<p align="center">
  <img src="assets/friday-logo.png" alt="Friday — neon logo" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>A personal development pipeline that brings Friday closer.</strong><br>
From a task to a draft MR: strong models plan and review, inexpensive models write the code.</p>

<p align="center">Python 3.11+ · macOS / Linux · <a href="LICENSE">MIT</a> · early version</p>

**English** · [Русский](docs/ru/README.md) · [简体中文](docs/zh-cn/README.md) · [日本語](docs/ja/README.md) · [한국어](docs/ko/README.md) · [Português (Brasil)](docs/pt-br/README.md) · [Español](docs/es/README.md) · [Deutsch](docs/de/README.md) · [Français](docs/fr/README.md) · [Bahasa Indonesia](docs/id/README.md)

---

```text
task → plan (strong model) → plan review (another vendor) → your approval
     → milestones: executor in a sandbox → gate → milestone review → browser acceptance of the UI
     → final review + blind check → draft MR
```

You approve the plan and accept the draft MR. The pipeline runs the plan within the configured budget; an exhausted budget or a broken environment comes back to the owner. Disputed review findings are sent to you together with the MR.

## Key points

- **Models by tier, not by name.** The profile holds a model catalog with a vendor and a tier (`frontier`, `strong`, `efficient`) for each model. Only `frontier` models review code, the reviewer is never weaker than the author, and where possible the reviewer is from a different vendor. An expensive model can be marked so that it runs once per task.
- **A cheap executor with fast feedback.** The executor runs the linter, type checks and tests itself in a sandbox without network access, and the gate gives it only the checks that failed.
- **You can see what is happening.** `friday status` shows the phase, the model, how long the agent has been silent, and how much has been spent on each task.
- **Several tasks at once.** Each task has its own branch and worktree. The machine settings define how many gates, browsers and runs of one vendor can run at the same time.
- **Branches named after the project.** By default `start` creates `<repository>/<task key>`, for example `octis/31`. The name comes from the Git `origin`; the profile can set its own template.
- **Bounded spending.** A profile can rule out frontier executors with `executor_tiers` and cap the size of the whole plan and the number of model runs with `budget`; `resume` does not reset the counters. The final review is saved per role, so a resume on unchanged inputs does not run it again.
- **Parallel milestones.** Independent milestones that are ready run in their own worktrees, two at a time by default; changed files they share block the integration.

## Quickstart

You need Python 3.11+, Git and Claude Code (Codex CLI is optional). The gate and browser acceptance also need Docker.

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

Friday speaks ten languages: `friday --lang ru …`, `FRIDAY_LANG=ru`, or `friday config language ru`. Agents always work in English; what you read is translated. Example profiles for Node and Python projects are in [examples/profiles](examples/profiles).

## Commands

| Command | What it does |
| --- | --- |
| `start KEY --profile P [--brief F] [--no-design]` | Creates the worktree and the plan, and has another model review the plan; with `design` in the profile the plan is checked against the Figma frame from the ticket |
| `plan KEY [--diff]` | Shows the plan with the unresolved findings; `--diff` shows what the latest revision changed |
| `approve KEY [--no-run]` | Approves the plan and starts the pipeline in the background |
| `status [KEY] [--json]` | Shows what the tasks are doing and how much has been spent |
| `decide KEY name "text" [--reopen M2]` | Records an owner decision. While the plan awaits approval, the planner revises the named milestones by it (without `--reopen` it chooses them itself), and only the changed ones are reviewed again ([ADR 0016](docs/adr/0016-decision-revises-the-plan.md)); after approval it reopens only the named milestones |
| `run KEY` / `resume KEY [--brief F]` / `stop KEY` | Runs in this terminal, continues, stops; `--brief` restores the original statement of an old task by its planning fingerprint |
| `serve` / `service print\|install\|uninstall\|check` | The machine's task queue: starts tasks in turn, puts back failed ones, notifies; runs as a launchd or systemd service ([guide](docs/guide.md#task-queue-friday-serve)) |
| `step [NAME] [KEY] [--profile P] [--dry-run]` | Runs a profile step (`steps`): a task step with the task key, a profile step with `--profile`; without a name, lists the steps and their state. The profile describes what a step does (building an image, a bundle and so on) ([ADR 0011](docs/adr/0011-profile-steps.md)) |
| `archive KEY [--force]` | Removes the worktree and the card; the branch stays |
| `journal KEY` | Updates the task journals (Obsidian, jsonl) |
| `doctor --profile P` | Checks that the machine and the profile are ready |
| `config language L` | Sets the language Friday speaks on this machine |

For details on the profile, models and boundaries, see the [guide](docs/guide.md). For why the pipeline is built this way, see the [pilot analysis](docs/process-redesign.md) (in Russian) and the [ADRs](docs/adr/) (0001–0006 in Russian).

## Development

```sh
git clone https://github.com/avocawo/friday.git && cd friday
python3 -m unittest discover -s tests
uvx ruff check .
python3 -m friday.i18n
```

The tests use temporary data and fake agents, with no network or Docker. How the code is organised is in [docs/architecture.md](docs/architecture.md); how to propose a change is in [CONTRIBUTING.md](CONTRIBUTING.md).

[Guide](docs/guide.md) · [Glossary](GLOSSARY.md) · [Contributing](CONTRIBUTING.md) · [License](LICENSE)
