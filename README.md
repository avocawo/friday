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

You take part twice: you approve the plan and you accept the draft MR. Between these two points the pipeline does not stop: disputed review findings are sent to you together with the MR.

## Key points

- **Models by tier, not by name.** The profile holds a model catalog with a vendor and a tier (`frontier`, `strong`, `efficient`) for each model. Only `frontier` models review code, the reviewer is never weaker than the author, and where possible the reviewer is from a different vendor. An expensive model can be marked so that it runs once per task.
- **A cheap executor with fast feedback.** The executor runs the linter, type checks and tests itself in a sandbox without network access, and the gate gives it only the checks that failed.
- **You can see what is happening.** `friday status` shows the phase, the model, how long the agent has been silent, and how much has been spent on each task.
- **Several tasks at once.** Each task has its own branch and worktree. The machine settings define how many gates, browsers and runs of one vendor can run at the same time.

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
| `start KEY --profile P [--brief F]` | Creates the worktree and the plan, and has another model review the plan |
| `plan KEY` | Shows the plan with the unresolved findings |
| `approve KEY [--no-run]` | Approves the plan and starts the pipeline in the background |
| `status [KEY] [--json]` | Shows what the tasks are doing and how much has been spent |
| `decide KEY name "text" [--reopen M2]` | Records an owner decision; reopens only the named milestones |
| `replan KEY [--brief F]` | Sends a plan that waits for approval back to the planner with your new decisions |
| `run KEY` / `resume KEY` / `stop KEY` | Runs in this terminal, continues, stops |
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
