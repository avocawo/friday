---
status: proposed
---

# Analysis tasks end in documents, not a merge request

Some work on a ticket happens before any code: accepting a mockup against a checklist, checking a statement against the code, preparing questions for product and design. It runs well as a project's own skill, which has the project's tools and documents, but Friday could not carry it. `friday start` needs a plan with milestones (`plan.validate`), approval runs milestones, and the task ends in a merge request. The owner ran such skills by hand, outside the card, its decisions and its status.

Friday has a second kind of task. A profile with `"kind": "analysis"` and `"toolset": {"plan": "project"}` names `project.analysis_command` with `{key}`. `friday start` runs it in a project session (ADR 0015) on the model of the `plan` role, with rules that forbid waiting for a person. When the ticket does not exist yet, `--brief` stands in for it and is appended to the prompt. The session returns a summary and questions in a small schema (`analysis.SCHEMA`). Questions stop the task, as in project execution. `status` shows them, and `friday decide` continues the same session with the answer (`analysis_session` on the card). A session that returns no questions has finished. Friday then runs `project.analysis_check`, if the profile names one, in the worktree with `{key}` and `{worktree}` filled in. A failing check goes back to the session once with its output, and a second failure fails the task. A passing check marks the task `done`. There are no milestones, gate, browser acceptance, final review or merge request. The documents live where the project's command writes them; `project.analysis_dir` names that place for `status`.

Each answer resumes the session, and a turn of a long analysis is expensive. `decide --hold` records an answer on the card without a turn, and the next answer without `--hold` sends all held answers in one turn. Work on the documents often goes on after the check passes, when the owner reads them. `friday reopen` resumes the session of a `done` task, or of one whose check failed, with a new instruction and runs the check again; a turn that does not return leaves the task as it was. The session runs for the owner but must not change the owner's Claude Code memory: Friday adds a deny rule for writes under `~/.claude/projects/*/memory/`, so the session still reads that memory. The rule covers the file tools, not a shell command that writes there.

## Consequences

- One profile cannot plan both kinds, because `kind` is a profile key. A project keeps a second local profile that extends the first.
- A key has one card, so the analysis card is archived before the same ticket starts development. Its documents are not in the worktree, so archiving keeps them.
- The analysis session has the owner's rights, as every project session does. Its boundaries are the project's permissions and hooks, and the check is the only verification Friday adds.
- `approve`, `run` and `resume` have nothing to run on an analysis card. `start` repeats a failed analysis, `reopen` continues a finished one.

## Considered Options

- **A plan with no milestones.** Approval, the run, the final review and delivery all assume milestones. Each would need its own exception.
- **The skill as a Friday plugin skill.** It would carry one project's paths and process into the public core, and it would still run outside the card.
- **A new card state for questions.** `stopped` with `questions` already means "waiting on the owner" to `status`, `serve` and the journals.
