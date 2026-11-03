# Friday guide

## Installation

1. Clone Friday and add `bin` to `PATH`, or set `FRIDAY_BIN`.
2. Install the CLIs that the agents need: Claude Code (`claude`) for Anthropic and Z.ai models, Codex CLI (`codex`) for OpenAI models. Sign in to them as usual.
3. For the gate and browser acceptance, install Docker. Browser acceptance also needs Chrome with the debugging port 9333 and a signed-in session in the test environment.
4. State is stored in `~/.local/share/friday` (override it with `FRIDAY_HOME`). Profiles and secrets do not enter the repository: the `profiles/` folder is in `.gitignore`.

### Plugin for Codex and Claude Code

```sh
codex plugin marketplace add "$PWD" && codex plugin add friday@friday
claude plugin marketplace add "$PWD" && claude plugin install friday@friday --scope user
```

The plugin teaches the agent to read `status` and `plan`. Starting a task, approving it and making decisions are left to you.

## How a task runs

1. **`start`** creates the branch `friday/KEY` from a fresh `origin/<base_branch>` and a worktree, locks in the role assignment, and writes the plan: milestones of 2–5 stages, files, a size estimate, code and browser criteria, and a digest of the project rules for the executor. The plan is first checked without a model (size, files, order, criteria). Then a model from a different vendor reviews it, and the planner answers the findings once: it accepts each one or rejects it with a reason.
2. **`plan`** shows the plan, the questions, the rejected findings, and the file overlaps with other active tasks.
3. **`approve`** approves the plan and starts the pipeline in the background (`--no-run` only approves; run it later with `run`).
4. **Milestone.** The executor works in one session per milestone and commits each stage. The gate runs on the commit. If it fails, the executor receives only the failed sections of the log. The milestone review looks at the diff of this milestone together with the gate result. The executor fixes or disputes the blocking findings; disputed findings are sent to you. Milestones with an interface go through browser acceptance in an isolated preview. After two failures in a row, a model of a higher tier takes the milestone in a new session.
5. **Final.** The final review and the blind check of the whole diff run in parallel. Blocking findings get one round of fixes.
6. **Draft MR** is opened if the profile sets `delivery`. Otherwise its text is saved in `merge-request.md` in the task folder.

The pipeline stops only when the gate never passed, the executor did nothing, the environment is unavailable, or the models allowed by the rules are used up. Disagreements in review do not stop it.

`decide KEY name "text" --reopen M2` records your decision. Only the named milestones and the milestones that depend on them are reopened. Without `--reopen`, the following milestones take the decision into account. `resume` continues a stopped or failed task. `stop` stops a running task: the agents end together with their child processes.

## Profile

A profile is a JSON file. The `extends` key includes another profile (for example, a shared model catalog); paths are relative to the profile file.

| Key | Purpose |
| --- | --- |
| `name`, `repository` | The name of the project and the path to its repository |
| `base_branch`, `worktree_root`, `branch_template`, `worktree_layout` | Where to create the task branch from and where to put it; the default template is `friday/{key}`, the layout is `key` or `branch` |
| `models`, `roles` | The model catalog and the role preferences (see below) |
| `engineering_policy` | Rules for the planner and the reviews |
| `rules_docs` | Documents with the project rules. Friday makes one digest of them for the planner and the plan reviewer (default `AGENTS.md`, `CLAUDE.md`, `docs/*.md`). The digest is stored by content hash and reused by tasks while the documents do not change; the planner does not read the documents in full |
| `repo_map`, `repo_map_budget`, `repo_map_mcp` | A repository map in the prompt of the planner and the plan reviewer, so that they do not explore the structure with tool calls. `none` (default): no map. `builtin`: the tracked files with their line counts. `graphify`: also the symbols of each file and how many files import it, taken from a [graphify](https://github.com/Graphify-Labs/graphify) graph (`uv tool install 'graphifyy[mcp]==0.9.100'`; it only parses code, no model is used; the graph is stored in `FRIDAY_HOME/maps`, not in the repository). In a measurement on CS-2 the map did not make planning cheaper (no map $6.19, `builtin` $7.08, `graphify` $7.97, one run each), so it is off by default. The map is cached by commit. If it is longer than `repo_map_budget` characters (60000), it is first shortened by dropping the symbols, then by collapsing folders. `repo_map_mcp: true` (only with `graphify`) gives both roles graph queries: `query_graph`, `get_node`, `get_neighbors`, `shortest_path`; not verified live on Codex. The plan reviewer receives the current text of the files named in the plan (up to 40000 characters) |
| `approval` | `owner` (default): the plan waits for `approve`. `auto`: the plan is approved automatically after review and the pipeline starts at once; the journal records that the plan was approved automatically |
| `limits` | `stage_files` and `stage_lines`: the limits of a stage (10 and 500) |
| `worktree_setup` | Commands that Friday runs in a new worktree before planning (dependencies and similar): argument arrays with the substitutions `{repository}` and `{worktree}`; they must be repeatable. Example for npm on macOS: `["sh", "-c", "[ -e node_modules ] \|\| cp -cR {repository}/node_modules node_modules"]` |
| `executor_commands` | Commands that the executor may run itself, in the exact form: `"python3 -m pytest"`, not `python` |
| `executor_deny_read` | Additional paths that the executor's shell cannot read |
| `milestone_rounds` | How many executor rounds a milestone gets (4) |
| `gate`, `gate_image`, `gate_mode`, `gate_*` | The check command in Docker; `git-diff` compares with the task base |
| `environment_errors` | Patterns of log lines that mean an environment failure, not a code failure |
| `browser`, `preview`, `behavior_test_policy` | The test environment, the preview and the rules of browser acceptance |
| `tracker` | Yandex Tracker (API or MCP), used to read the task description |
| `journal` | Where to write the task journal: `[{"kind": "markdown", "dir": "..."}, {"kind": "jsonl", "dir": "..."}]`; `journal_dir` is a short form for a single markdown journal |
| `delivery` | `{"transport": "github" \| "gitlab" \| "none", "target_branch": "..."}` and the key `github.repository` or `gitlab.base_url` + `gitlab.project` |

### Models and roles

```json
"models": {
  "fable": {"model": "claude-fable-5-1", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "strength": 1, "scarce": true, "effort": "high"},
  "opus-high": {"model": "claude-opus-5-5", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "effort": "high"},
  "glm-high": {"model": "glm-5.3", "vendor": "zai", "driver": "claude", "tier": "strong", "effort": "high",
               "token_file": "~/.config/friday/zai.env", "token_key": "ZAI_API_KEY"}
},
"roles": {"plan": ["fable"], "develop": ["glm-high"], "review": ["opus-high"]}
```

- `driver`: what runs the model, `claude` or `codex`. Z.ai goes through Claude Code; the token is read from a file that only the owner can access.
- `tier`: `frontier`, `strong` or `efficient`; `strength` orders the models within a tier.
- `effort`: the depth of reasoning (`low` … `max`); one model can be described several times with different effort.
- `scarce`: an expensive model; it is used once per task if the rules allow the task to go without it.
- `price`: the price per million tokens by kind (`input`, `output`, `cache_read`, `cache_write`), used to estimate spending when the vendor does not report a price.

Roles: `plan`, `plan-review`, `develop`, `review`, `final-review`, `blind-code`, `browser`. The lists in `roles` are preferences: if the list has no suitable model, Friday takes another one from the catalog. Hard rules: only `frontier` models review code, the reviewer is not weaker than the author of the work, and the blind check is not done by the author of the code. Soft rules, in order: not the same model as the author; an expensive model once; a different vendor; the blind check not by the author of the plan. A soft rule can be broken only when there is no other way, and the violation is recorded in the task history.

An example role assignment is in [examples/models.json](../examples/models.json).

## Journal

The task card is the only source of truth. The journals show it and update themselves after significant events: the plan is ready and approved, a milestone is done or failed, an owner decision, the final review, the MR, a stop. To update them by hand, run `friday journal KEY`.

- `markdown`: a page `KEY.md` and a shared overview page that lists all tasks. For Obsidian, set the vault folder: the pages have front matter (key, state, spending, the tag `friday`), and tasks are linked with wiki links. A task page contains the plan with its assumptions and unresolved findings, the milestones with the executor summary and the commits, the disputed findings, the decisions, the spending by role, and the timeline.
- `jsonl`: all events of the task, for analysis in spreadsheets or dashboards.

Templates are Markdown with the substitutions `{{key}}`, `{{state}}`, `{{status}}`, `{{plan}}`, `{{milestones}}`, `{{disputes}}`, `{{decisions}}`, `{{costs}}`, `{{timeline}}`, `{{notes}}`. Friday takes the template from the `template` field of the target, then `.friday/task.md` in the project repository, then the built-in one ([friday/templates](../friday/templates)). The text between `<!-- friday:notes -->` and `<!-- /friday:notes -->` is yours: it is kept at every update. Friday does not overwrite a page that it did not write.

## Machine settings

`FRIDAY_HOME/machine.json` sets the shared resources for all tasks:

```json
{"slots": {"gate": 2, "browser": 2, "vendor:zai": 2}, "ports": [5200, 5399]}
```

By default, one gate and two browsers run at the same time, and vendors are not limited. Vendor quotas are tracked automatically: a run that hits a limit records when the quota returns, and until then the vendor is skipped when models are chosen.

## Boundaries

- **Executor sandbox.** The executor's shell (Claude Code) runs in a sandbox: no network, no reading of `~/.ssh`, `~/.aws`, `~/.config` and similar, writing only to the worktree and a temporary folder, and no running outside the sandbox. Commands that are not in `executor_commands` and are not git commands are rejected. Codex runs in its own sandbox with the same boundaries.
- **Reviewers** only read. The blind check runs in an empty folder.
- **The gate** runs in a container without network access, on a snapshot of the commit mounted read-only.
- **The browser** receives only Friday's tools for one preview; bypassing authorization and reading the headers of authorized requests are forbidden.
- **Publishing.** Friday pushes only the branch of its own task and opens only a draft MR/PR. It does not overwrite someone else's branch, merge, or deploy.
- **Not yet verified live:** roles on Codex, browser acceptance and publishing to GitLab have been verified only by tests with fakes.
