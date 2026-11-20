# Architecture decision records

An ADR records one decision about how Friday is built: the context, what was decided, and the options that were turned down. Write one when a change:

- changes how the pipeline works (phases, roles, who reviews what, when the owner is involved);
- moves or weakens a boundary (sandbox, network, secrets, what agents may read or run);
- adds a concept that other parts will depend on (a new profile section, a new kind of state).

A bug fix, a refactoring or a new option inside an existing design does not need one.

## How

1. Copy [`template.md`](template.md) to `NNNN-short-title.md` with the next number.
2. Open it with `status: proposed` in the pull request that implements it, or in a pull request of its own when you want agreement first.
3. Set `status: accepted` when it is merged. When a later decision replaces it, set `status: superseded by NNNN` and leave the text as it was.

New ADRs are written in English. ADRs 0001–0006 were written in Russian before the project opened up; the terms they use are in the [glossary](../../GLOSSARY.md).

| ADR | Decision |
| --- | --- |
| [0001](0001-milestone-pipeline.md) | Pipeline by milestones instead of three checks per stage |
| [0002](0002-model-tiers.md) | Models chosen by tier and vendor, not by name |
| [0003](0003-executor-tools-and-gate.md) | The executor runs checks itself; the gate is isolated |
| [0004](0004-session-per-milestone.md) | One executor session per milestone |
| [0005](0005-shared-resources.md) | Counted slots shared between tasks |
| [0006](0006-journal-from-events.md) | Journals are rendered from events |
| [0007](0007-universal-core-and-languages.md) | A core without one project's stack; agents in English, people in their language |
