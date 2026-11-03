# Friday

A personal development pipeline: it takes a task from a plan to a draft MR using models from different vendors and checks their work.

## Work

**Task**:
A unit of work for the owner, with its own card, branch and worktree; usually matches a tracker ticket.
_Avoid_: job, mission

**Plan**:
The list of milestones and stages for a task, with acceptance criteria, approved by the owner.

**Milestone**:
A group of 2–5 consecutive stages that passes the gate and review as a whole.
_Avoid_: phase

**Stage**:
The smallest part of a plan; it ends with one commit.
_Avoid_: step

**Criterion**:
A verifiable acceptance condition for a stage; it is either a code criterion (checked against the diff) or a behavior criterion (observed in the browser).

**Profile**:
The JSON file that configures Friday for one project (repository, models, roles, gate, browser, tracker, journal, delivery); it may extend another profile through the `extends` key.

## Roles

**Owner**:
The person who approves the plan, accepts the draft MR and resolves disputed findings.
_Avoid_: operator, user

**Planner**:
The model that writes the plan.

**Executor**:
The model that writes the code of a milestone.
_Avoid_: developer

**Reviewer**:
The model that reviews the plan, a milestone or the final diff and gives a verdict on the criteria.

**Model tier**:
The capability class of a model: `frontier`, `strong` or `efficient`.

**Role assignment**:
The assignment of specific models to the roles of a task, fixed before the plan is approved.

## Checks

**Gate**:
Deterministic checks without models, run in an isolated container on a specific commit.
_Avoid_: CI, validation

**Milestone review**:
A review of the milestone diff, with the gate log, by a reviewer from a different vendor.

**Final review**:
A review of the whole task diff before the MR, including the blind check of the code.

**Browser acceptance**:
Observation of the behavior criteria of a milestone in the browser on a local test environment.
_Avoid_: blind-behavior

**Disputed finding**:
A reviewer finding that the executor disagreed with and that was sent to the owner through the journal.

## Records

**Card**:
Friday's machine state of a task: events, milestones, verdicts, spending.

**Journal**:
The readable history of a task in Obsidian, built from the card with templates.
