---
name: friday
description: Use the Friday assistant to inspect or run a task or project with its local profile. Use when the user invokes Friday or asks to operate its task workflow.
---

# Friday

Use the installed Friday CLI as the source of truth for commands and state. Find it through `FRIDAY_BIN`, then `PATH`, then `~/Projects/friday/bin/friday`; if unavailable, explain that the separate Friday checkout and CLI need installation. Do not install dependencies or change profiles without the user's request.

Run `friday --help` or the relevant command's `--help` before using a command. Determine the intended project profile from the user's context; `friday doctor --profile <path>` checks its configured environment. Profiles and credentials remain local; never paste credential values or private project data into prompts or reports. Friday sends the configured engineering policy to its model runners.

Use `take-stock`, `status`, and `long-weekend backlog` for read-only inspection. `home-stretch` and `long-weekend start`, `resume`, `next`, `cruise`, or `service install` can run models, execute checks, and, when configured, push and create a draft MR or PR; the service repeats these actions in the background. Perform them only within the user's requested scope and existing authorization. Do not merge, deploy, or access production without explicit authorization. If isolation or a check fails, report the actual failure instead of bypassing it.
