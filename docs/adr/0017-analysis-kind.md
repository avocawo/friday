---
status: proposed
---

# Analysis tasks end in documents, not a merge request

Some work on a ticket happens before any code: accepting a mockup against a checklist, checking a statement against the code, preparing questions for product and design. It runs well as a project's own skill, which has the project's tools and documents, but Friday could not carry it. `friday start` needs a plan with milestones (`plan.validate`), approval runs milestones, and the task ends in a merge request. The owner ran such skills by hand, outside the card, its decisions and its status.

Friday has a second kind of task. A profile with `"kind": "analysis"` and `"toolset": {"plan": "project"}` names `project.analysis_command` with `{key}`. `friday start` runs it in a project session (ADR 0015) on the model of the `plan` role, with rules that forbid waiting for a person. When the ticket does not exist yet, `--brief` stands in for it and is appended to the prompt. The session returns a summary and questions in a small schema (`analysis.SCHEMA`). Questions stop the task, as in project execution. `status` shows them, and `friday decide` continues the same session with the answer (`analysis_session` on the card). A session that returns no questions has finished. Friday then runs `project.analysis_check`, if the profile names one, in the worktree with `{key}` and `{worktree}` filled in. A failing check goes back to the session once with its output, and a second failure fails the task. A passing check marks the task `done`. There are no milestones, gate, browser acceptance, final review or merge request. The documents live where the project's command writes them.

## Consequences

- One profile cannot plan both kinds, because `kind` is a profile key. A project keeps a second local profile that extends the first.
- A key has one card, so the analysis card is archived before the same ticket starts development. Its documents are not in the worktree, so archiving keeps them.
- The analysis session has the owner's rights, as every project session does. Its boundaries are the project's permissions and hooks, and the check is the only verification Friday adds.
- `approve`, `run` and `resume` have nothing to run on an analysis card. `start` repeats a failed analysis.

## Considered Options

- **A plan with no milestones.** Approval, the run, the final review and delivery all assume milestones. Each would need its own exception.
- **The skill as a Friday plugin skill.** It would carry one project's paths and process into the public core, and it would still run outside the card.
- **A new card state for questions.** `stopped` with `questions` already means "waiting on the owner" to `status`, `serve` and the journals.
