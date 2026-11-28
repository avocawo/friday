---
status: proposed
---

# The planner weighs design options by size, and the plan review looks for a smaller one

A plan for nfront DVIZHDEV-7820 (uploading `.sig` signatures to a company representative's documents) turned down "one upload operation that picks its mutation by target, and one generic queue" because each queue slot "would hold both mutations and the body would branch on kind". On top of that the planner set itself a constraint, "do not touch the public API of documents". The result was a shared base64 operation, two operation wrappers, a shared queue and two queue wrappers: eight files in `model/`. A colleague then built the rejected option in four files and 170 fewer lines, and no project rule forbids it. The reasons for turning it down were matters of taste, and the self-imposed constraint was treated as a given rather than an assumption with a cost.

The planner prompts (`PLAN_RULES`, `PROJECT_PLAN_RULES` in `friday/plan.py`) now require that, for each design choice with more than one workable option, assumptions list every option considered with its estimated files and added lines. The planner takes the option with fewer new modules and lines unless a project rule it cites or a failure it can show rules it out; "it would branch on a kind", "each instance would hold both dependencies" or "it is cleaner" is not such a reason. A constraint the planner sets itself rather than takes from the ticket or a rule, such as keeping a public API unchanged, is an assumption recorded with the lines it costs. The plan reviewer (`REVIEW_RULES`) looks for an option rejected without a cited rule or a shown failure, a self-imposed constraint whose cost is not counted, and a simpler design the plan did not consider. Each such finding is minor, and blocking when the plan's design is larger by more than about a third in files or added lines.

## Consequences

- Plans get longer assumptions, and the owner sees at approval which options were weighed and what each would cost.
- A plan that grows the code for taste gets a finding, blocking when the gap is above about a third, and the planner answers it as it answers any other.
- The rule compares ways to build the same behavior. It is not "fewer lines at any cost": merging unrelated work into one function is not a smaller option, and the prompt says so.
- Size estimates are the planner's and the reviewer's guesses; "about a third" is a threshold for judgment, not a number Friday checks.

## Considered Options

- **"Prefer less code" as a general rule.** Without tying it to a comparison of options and to what counts as a reason to reject one, it invites catch-all functions that do several jobs. Turned down.
- **A feasibility check on plan size.** `validate` cannot know the size of an option the plan did not write down. Turned down.
- **Leave it to the milestone reviews.** By then the design is built and the extra files are in the diff; changing it costs a reopen. Turned down.
