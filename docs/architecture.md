# Architecture

This page is for people changing Friday. It tells you what happens in a task, which module owns each step, and where the state lives. The reasons behind the design are in the [ADRs](adr/); terms are defined in the [glossary](../GLOSSARY.md).

## A task, end to end

```text
friday start KEY ──► workspace ─► plan.make ─────────────────────────► card: awaiting_approval
                      (branch,    (rules digest, repository map,
                       worktree)   planner, feasibility checks,
                                   plan review by another vendor,
                                   one revision)
friday approve KEY ─► cli.launch ─► milestone.run_task (in the background)
                                      for each milestone (parallel when allowed):
                                        executor session ─► gate ─► milestone review ─► fixes or disputes
                                        browser acceptance for ui milestones
                                    final.final_review (final review + blind check) ─► fixes
                                    final.deliver ─► draft MR (vcs) or saved text
```

The owner acts twice: approving the plan, and reading the draft MR with its disputes. `friday decide` records an owner decision and reopens only the milestones it names.

## Modules

| Area | Modules | What they own |
| --- | --- | --- |
| Command line | `cli.py`, `__main__.py`, `bin/friday` | Argument parsing, the language choice, starting the background run. `bin/friday` only runs `cli.main` from a checkout. |
| Configuration | `profile.py`, `models.py`, `credentials.py` | JSON profiles with `extends`; the model catalog, roles and tiers and the rules for choosing reviewers (ADR 0002); vendor quotas; tokens from the environment or owner-only files. |
| Task state | `card.py`, `state.py`, `status.py`, `tasks.py`, `locks.py` | The card (`card.json`) and its event log (`events.jsonl`); `friday status`; stop and archive. |
| Planning | `plan.py`, `repomap.py`, `tracker.py` | Planner and plan reviewer prompts and schemas; feasibility checks that need no model; the optional repository map; reading the ticket (Yandex Tracker, GitHub Issues) or `--brief`. |
| Running agents | `runner.py`, `codex_policy.py` | One Claude Code or Codex session: its sandbox, allowed tools, live status, usage and timeouts. |
| Milestones | `milestone.py`, `gate.py`, `workspace.py`, `resources.py` | The executor/review loop, parallel milestones and their merge, the gate in a networkless read-only container (ADR 0003), slots and preview ports shared between tasks (ADR 0005). |
| Browser acceptance | `final.py` (`browser`), `isolated_preview.py`, `local_preview.py`, `browser_session.py`, `browser_auth.py`, `browser_probe.py`, `browser_protocol.py`, `browser_mcp.py` | Running the app snapshot without network, a Chrome MCP session limited to the preview and one staging origin, borrowing a staging session without showing its token to an agent. |
| Finishing | `final.py`, `vcs.py` | Final review and blind check, the MR text, draft MRs on GitHub (`gh`) or GitLab (`glab`). |
| What people read | `i18n.py`, `translate.py`, `journal.py`, `templates/`, `locales/` | Message catalogs and the language choice; translating text written by agents; Markdown and JSONL journals (ADR 0006). |

## Where state lives

Everything is under `FRIDAY_HOME`, by default `~/.local/share/friday`:

| Path | Contents |
| --- | --- |
| `cards/<KEY>/card.json` | The task: state, cast (role → model), plan approval, milestones, disputes, decisions. The only source of truth. |
| `cards/<KEY>/events.jsonl` | Everything that happened, one JSON object per line; journals and costs are computed from it. |
| `cards/<KEY>/plan.json`, `merge-request.md`, `runs/<run>/` | The approved plan, the MR text, and each agent run's prompt, transcript and status. |
| `digests/`, `maps/` | Caches shared between tasks: rules digests by the content of the rules documents, repository maps by commit. |
| `translations/<lang>.json` | Cached translations of text written by agents. |
| `quota.json`, `slots/`, `ports.json`, `locks/`, `machine.json` | Vendor quotas, counted slots, preview ports, process locks and this machine's limits. |
| `config.json` | Settings of this machine, such as the interface language. |

Worktrees live where the profile's `worktree_root` says; each task has its own branch (`branch_template`).

## Languages

Agents work only in English: prompts ask for English, and everything stored in `FRIDAY_HOME` is English. People read Friday in the language they choose:

- **Fixed text** (help, status, doctor, errors, headings) goes through `i18n._()` and the catalogs in `friday/locales/`. A message's key is its English text, so English needs no catalog. Errors keep an English template and parameters (`FridayError(code, "Task {key} …", key=…)`), so logs stay English and `i18n.error()` shows them translated.
- **Text written by agents** goes through `translate.py`: one structured run of the `translate` role (the cheapest model in the catalog), cached by text and language.

When you add or change a message, run `python -m friday.i18n` to see what each catalog is missing, and add the translations. The test suite fails while a catalog is incomplete.

## Boundaries you must keep

- The executor edits files only inside its worktree, runs only the profile's `executor_commands`, and has no network (`runner.py`, `codex_policy.py`).
- The gate and previews run in containers with `--network none` and a read-only root, on a committed snapshot without `.env` files.
- The browser reaches only the local preview and the one staging origin; `browser_session.stage_origin` accepts only explicit HTTPS hosts named as test environments.
- Secrets never go into prompts, events or logs; `doctor` reports what is missing without values.

A change that touches one of these needs a test that proves the boundary still holds.

## Tests

`python -m unittest discover -s tests` runs everything in about a minute. Agents are fake executables (`tests/test_runner.py`), repositories are temporary, and nothing needs network or Docker. `bin/test-offline` runs the same suite in a networkless container.
