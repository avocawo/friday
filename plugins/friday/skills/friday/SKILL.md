---
name: friday
description: Use the Friday pipeline to plan, run and inspect development tasks with a local project profile. Use when the user invokes Friday or asks about a task Friday is running.
---

# Friday

Use the installed Friday CLI as the source of truth for commands and state. Find it through `FRIDAY_BIN`, then `PATH`; if it is unavailable, say that Friday needs installing (`uv tool install` or `pipx install` from its repository). Do not install dependencies or change profiles without the user's request. Run `friday --help` or a command's `--help` before using it.

The pipeline: `friday start KEY --profile P` prepares a worktree, writes a plan and has another vendor's model review it; the owner reads it with `friday plan KEY` and approves with `friday approve KEY`, which starts the run in the background; Friday then executes milestones, gates and reviews them, runs the final review and opens a draft MR when the profile configures delivery.

Read-only, safe at any time: `friday status`, `friday status KEY`, `friday plan KEY`, `friday doctor --profile P`. When Friday speaks a language other than English, `friday plan` translates new plan text once with an inexpensive model and caches it. Report what they show; `status` names the phase, model, how long it has been quiet and what it costs.

Only on the user's explicit request: `start` and `run`/`resume` (they run models and spend quota), `approve` (the owner's decision; never approve on your own judgment), `decide` (records an owner decision and may reopen milestones; it runs no model), `replan` (sends a plan waiting for approval back to the planner with the decisions recorded since it; runs models and spends quota), `stop`, `archive` (removes the worktree). Never merge, deploy or touch production. Profiles and credentials stay local; never paste credential values or private project data into prompts or reports. If a check or isolation fails, report the actual failure instead of working around it.
