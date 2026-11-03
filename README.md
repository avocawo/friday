<p align="center">
  <img src="assets/friday-logo.png" alt="Friday — neon logo" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>A personal development pipeline that brings Friday closer.</strong><br>
From a task to a draft MR: strong models plan and review, inexpensive models write the code.</p>

<p align="center">Python 3.9+ · macOS / Linux · <a href="LICENSE">MIT</a> · early version</p>

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

You need Python 3.9+, Git, and Claude Code and/or Codex CLI. The gate and browser acceptance also need Docker.

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

## Commands

| Command | What it does |
| --- | --- |
| `start KEY --profile P [--brief F]` | Creates the worktree and the plan, and has another model review the plan |
| `plan KEY` | Shows the plan with the unresolved findings |
| `approve KEY [--no-run]` | Approves the plan and starts the pipeline in the background |
| `status [KEY] [--json]` | Shows what the tasks are doing and how much has been spent |
| `decide KEY name "text" [--reopen M2]` | Records an owner decision; reopens only the named milestones |
| `run KEY` / `resume KEY` / `stop KEY` | Runs in this terminal, continues, stops |
| `archive KEY [--force]` | Removes the worktree and the card; the branch stays |
| `journal KEY` | Updates the task journals (Obsidian, jsonl) |
| `doctor --profile P` | Checks that the machine and the profile are ready |

For details on the profile, models and boundaries, see the [guide](docs/guide.md). For why the pipeline is built this way, see the [pilot analysis](docs/process-redesign.md) (in Russian) and the [ADRs](docs/adr/) (in Russian).

## Development

```sh
docker build -f Dockerfile.test -t friday-test:local .
./bin/test-offline
```

The tests use temporary data and fake agents in a container without network access.

[Guide](docs/guide.md) · [Glossary](GLOSSARY.md) · [Contributing](CONTRIBUTING.md) · [License](LICENSE)
