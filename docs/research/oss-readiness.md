# Open-source readiness audit

Audit of `main` at `53a25af` (2026-11-03) as a newcomer would meet the repository, compared with practices of well-run Python CLI projects. Written in English because the user documentation is moving to English as the canonical language.

## Summary

- **The code is publishable as is.** The tracked tree and all 21 commits (single author, GitHub noreply address) contain no secrets, no `/Users/…` paths, no company names, hosts or Tracker queue keys. The private project profiles live in `profiles/`, which is in `.gitignore` and never entered history. Nothing needs deleting or a history rewrite.
- **The first run fails.** Following the README quickstart with the shipped examples ends in `Не готово` with three `✗` even on the owner's machine (details below). This is the most visible problem for a newcomer.
- **There is no packaging, no CI and almost no community-health scaffolding.** Friday is stdlib-only, so packaging and a CI matrix are cheap.
- **Some of the core is one project's frontend stack.** The browser preview hardcodes Vite, `VITE_API_APOLLO_URI` and a Chrome debug port of 9333. These are not secrets, but they will not work for other people's projects.

## 1. Install and first run

### What exists

- No `pyproject.toml`, `setup.py` or `requirements*.txt`. The CLI is `bin/friday`, which adds the repository root to `sys.path` (`bin/friday:10`). `VERSION = "0.2.0"` is hardcoded there and repeated in `plugins/friday/.claude-plugin/plugin.json` and `plugins/friday/.codex-plugin/plugin.json`. There are no git tags.
- **Dependencies:** standard library only (every import across `friday/`, `bin/` and `adapters/` checked). `fcntl` limits Friday to POSIX, which matches the README's "macOS / Linux".
- **Python versions:** README and CONTRIBUTING say 3.9+. The suite (121 tests, 1 skipped, about 50 s) passes on 3.9.6 (macOS system Python) and on 3.13.9. According to the [Python devguide](https://devguide.python.org/versions/), 3.9 reached end of life on 2025-10-31 and 3.10 on 2026-10-01. The oldest supported branch is now 3.11.
- **External tools,** as checked by `friday doctor` (`friday/doctor.py`):
  - `git` (the repository check);
  - `claude` (plus `sandbox-exec` on macOS or `bwrap` on Linux for the executor sandbox) when the profile has a model with the `claude` driver;
  - `codex` and its sandbox for the `codex` driver;
  - API tokens for vendors with their own endpoints (Z.ai);
  - `graphify` and `graphify-mcp` when `repo_map` is `graphify`;
  - Docker and the gate image when `gate` is set;
  - Node, `chrome-devtools-mcp` with `--allowedUrlPattern` and a free preview port when `browser` is set;
  - `gh` or `glab` for delivery.

  Missing `executor_commands`, `gate`, delivery and tracker are only warnings.

### The quickstart, executed

The README steps were run in a scratch directory: a fresh `git init` repository at `../../my-project` relative to `profiles/`, and `FRIDAY_HOME` pointing into the scratch directory.

```text
✓ profile ✓ repository ✓ models ✓ claude_cli ✓ executor_sandbox ✓ codex_cli_and_sandbox
✗ token:glm-high: Cannot read token file: FileNotFoundError
✗ token:glm-flash: Cannot read token file: FileNotFoundError
✗ gate_image: Gate image is unavailable locally: my-project-gate:local
! delivery  ! tracker
Раскладка: plan → claude-fable-5-1 high, develop → glm-5.3 high, plan-review → gpt-6-astra high, …
Не готово: исправьте пункты со ✗.   (exit 1)
```

Problems for a newcomer:

1. `examples/models.json` assumes three vendors (Anthropic, OpenAI through Codex, and Z.ai with `~/.config/friday/zai.env`). `models.cast` puts the Z.ai model first for `develop` even when its token file is missing, so a newcomer with only Claude Code cannot become ready without editing the catalog.
2. `examples/project-profile.json` sets a `gate` and `gate_image: my-project-gate:local`, but the repository has no instructions for building a gate image, and no example of one. Its `executor_commands` are specific to Vue (`vue-tsc`, `vitest`).
3. The clone URL is SSH (`git@github.com:…`), which fails for people without a GitHub key. The HTTPS form works for everyone.
4. The relative path `../../my-project` resolves against the profile file (`friday/profile.py:46`), so it points two levels above `profiles/`. This works, but the README does not explain it.
5. CLI help and `doctor` summaries are in Russian, while the check reasons are in English. This is a coordination point with the translation work (see §6).

### Practice

The [PyPA guide to `pyproject.toml`](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/) recommends:

- `[project.scripts]` for console commands, so that `pipx install` / `uv tool install` puts `friday` on `PATH`;
- `requires-python` as the field that actually restricts installation (classifiers do not);
- an SPDX `license` expression together with `license-files`;
- optionally, a dynamic `version` taken from the package.

[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) replaces long-lived API tokens with short-lived OIDC tokens issued to GitHub Actions.

To package Friday, these points must change:

- `friday/final.py:13` finds `adapters/browser_mcp.py` outside the package (`parent.parent / "adapters"`), so it would not be present after an install. It should move into the package, for example as `friday/browser_mcp.py`, run with `python -m`.
- `friday/templates/*.md` must be shipped as package data.
- `tests/test_plan.py:14` and `tests/test_card.py:15` run `bin/friday` directly. They should keep working, so `bin/friday` should remain a thin shim around `friday.cli:main`.
- `plugins/friday/skills/friday/SKILL.md` looks for the CLI at `FRIDAY_BIN`, then on `PATH`, then at `~/Projects/friday/bin/friday`. The last one is the owner's layout. After packaging, `PATH` is enough.
- Profiles currently live inside the clone (`profiles/`). An installed package has no clone, so profiles need a documented default location, such as `~/.config/friday/profiles/`, or the convention of always passing `--profile` / `FRIDAY_PROFILE`.
- **PyPI names:** `friday` and `friday-cli` are taken; `friday-pipeline`, `fridayctl` and `friday-agents` were free on 2026-11-03. The import package and the command can stay `friday` whatever the distribution is called.

## 2. Community health files

| File | State | Reference |
| --- | --- | --- |
| `LICENSE` | MIT, "Copyright (c) 2026 Friday contributors" | [Scorecard: License](https://github.com/ossf/scorecard/blob/main/docs/checks.md) |
| `CONTRIBUTING.md` | Exists, 10 lines, Russian. Covers policy (project-specific behaviour belongs in profiles, keep Python 3.9, add few dependencies, run the tests). Does not cover setup, branching, commit style, PR flow, ADRs or the glossary. | [GitHub: community health files](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/creating-a-default-community-health-file) |
| `CODE_OF_CONDUCT.md` | Missing | [Contributor Covenant](https://www.contributor-covenant.org/) (the current version is 3.0) |
| `SECURITY.md` | Missing. This matters more than usual: Friday runs agents, sandboxes and network allowlists. | Scorecard: Security-Policy; [GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository) |
| Issue and PR templates | Missing (`.github/` does not exist) | [GitHub: issue and PR templates](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests) |
| `CHANGELOG.md` | Missing | [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) |
| Versioning and releases | `0.2.0` in three places, no tags, no release process | [SemVer](https://semver.org/) (0.x: anything may change) |

GitHub reads `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md` and `SUPPORT.md` from the repository root, `.github/` or `docs/`. Issue templates live in `.github/ISSUE_TEMPLATE/`.

## 3. CI

- **Current state:** no `.github/workflows`. Tests run locally with `python3 -m unittest discover -s tests` or in Docker with `bin/test-offline`. That runs `Dockerfile.test` (python:3.13-slim and git) read-only, with no network and all capabilities dropped.
- **Native runs:** the tests need neither Docker nor the network on the host. They pass on 3.9 and 3.13, so a GitHub Actions matrix can run `unittest` directly on `ubuntu-latest` and `macos-latest`. `bin/test-offline` remains the hermetic local path.
- **Lint baseline:** `ruff check --select E9,F --target-version py39` reports only four unused imports in tests, all fixable automatically. Nothing else is flagged.
- **What the [OpenSSF Scorecard checks](https://github.com/ossf/scorecard/blob/main/docs/checks.md) imply for the workflow design:**
  - CI-Tests: run on every PR.
  - Token-Permissions: set `permissions: contents: read` at the top of the workflow.
  - Pinned-Dependencies: pin actions by commit SHA and let Dependabot update them.
  - Dangerous-Workflow: no `pull_request_target`, and no untrusted `${{ }}` inside `run:`.
  - Branch-Protection and Code-Review: repository settings, which are the owner's to change.
  - SAST: optionally, CodeQL.
  - Packaging, Signed-Releases: only if PyPI publishing is chosen; Trusted Publishing produces attestations.

## 4. Hygiene

### Scope of the scan

- Tracked files and every one of the 21 commits on all refs, searched for `avocawo`, `/Users/`, the company and stand domains and the project names seen in the owner's private notes, Tracker-style keys (`[A-Z]{3,}-\d+`) and all external hosts.
- A regex secret scan of all history: AWS, GitHub, GitLab and Anthropic tokens, OpenAI-style keys, Slack tokens, private-key headers, Yandex OAuth tokens, JWTs and `password|secret|token = "…"` assignments. `gitleaks` and `trufflehog` are not installed, so this scan is weaker than a dedicated tool and should be repeated with one before the repository goes public.
- The names of every file ever added.

### Results

- **Secrets:** none. The only match is the redaction pattern inside the deleted `friday/github.py`. The only credential-sounding filenames are `friday/credentials.py`, which is the token loader with no values, and `docs/research/token-cost.md`.
- **Personal data:** commit authors use `38790699+avocawo@users.noreply.github.com`. The only owner-specific strings are the clone URL `github.com/avocawo/friday` (expected) and the `~/Projects/friday/bin/friday` fallback in `SKILL.md`.
- **Company data:** none tracked, now or in history. `docs/process-redesign.md` and ADR 0001 describe a "pilot on a frontend project (tasks A and B)" with costs and model names but no identifiers. The owner should still read them once and decide whether they want that pilot narrative public.
- **`profiles/` (ignored, not in history):** contains the owner's private project files (the gate Dockerfile and scripts, a profile, notes, a changelog and `legacy/`). Keep them out of the repository. If the owner wants a public worked example of a gate image, write a fresh generic one rather than adapting these.
- **Project-specific behaviour in the core:** this is not a leak, but it is hardcoded and contradicts CONTRIBUTING's first rule.
  - `friday/isolated_preview.py:102` starts `/app/node_modules/vite/bin/vite.js` on port 5173 and sets `VITE_API_APOLLO_URI` and `VITE_APP_ENV`.
  - `friday/browser_auth.py:25` and `friday/browser_session.py:84` parse `VITE_API_APOLLO_URI` out of page scripts.
  - `friday/browser_probe.py:33` hardcodes `--browserUrl=http://127.0.0.1:9333`.
  - `docs/guide.md` tells users to run Chrome on port 9333 and to be signed in to "the stand".

  For other users, browser acceptance therefore works only with a Vite + Apollo frontend.
- **Other:** `git worktree list` shows a prunable worktree, `/private/tmp/friday-pilot-work.*`. It is local only and does not ship.

## 5. Developer documentation

- **Architecture:** nothing gives an overview of how the modules fit together. The roughly 4,600 lines in about 30 modules under `friday/` have good docstrings. The glossary (`GLOSSARY.md`) and the ADRs explain *why* but not *where*. A short `docs/architecture.md` would map the pipeline phases to modules and name the state files under `FRIDAY_HOME`. It should cover `runner`, `plan`, `milestone`, `gate`, `final`, `resources`, `state`, `models`, `profile` and the browser modules.
- **Tests:** two working paths (native `unittest` and Docker `bin/test-offline`), described briefly in the README. They take about 50 s, use fake agents (`tests/`) and need no network.
- **ADRs:** six ADRs in `docs/adr/` with `status` front matter and "Considered Options" sections, in Russian. There is no template and no README explaining when to write one. The `domain-modeling` skill the owner uses covers ADR format, and `GLOSSARY.md` is its counterpart.
- **Glossary:** `GLOSSARY.md`, 72 lines, Russian, current.

## 6. Universality: what ties the core to one team

Friday's agent prompts are already neutral: they say "write in the task's language", and models, roles, commands and the gate come from the profile. What remains is ordered below by how much it blocks a stranger's core path (install → `start --brief` → `approve` → draft MR).

### Tier 1: affects everyone, cheap to fix

| Where | What is hardcoded | Replacement |
| --- | --- | --- |
| `final.py:121-142` (MR body), `journal.py` (44 strings), `status.py` (16), `bin/friday` (30 help strings), `doctor.render`, `models.py` (9), `plan.py` (18) | Output is in Russian, so a non-Russian team receives a draft MR it cannot read | Message catalogs with a language chosen by the user; English is the default (see below) |
| `plan.py:69` | Tells the planner the Russian words for "milestone" and "stage" | Remove; the glossary covers this |
| `models.py:16` `VENDOR_ENDPOINTS = {"zai": …}` | Only one non-Anthropic endpoint is possible | A `base_url` field on each catalog entry (`zai` stays as a built-in default). This opens any Anthropic-compatible endpoint. Resource slots are already keyed on `vendor:<name>` generically |
| `examples/` | One Vue profile and a three-vendor model catalog | A Claude-only catalog, plus Node, Python and generic profile examples |

### Language: agents work in English, people read in their own language (owner request, 2026-11-03)

The owner's rule: **agents work only in English; everything a person reads is given in the chosen language.**

This changes the agents, not only the CLI. Today the prompts say "write in the task's language":

- `plan.py:68,95,233`
- `milestone.py:48,55`
- `final.py:24,31,35`

So plans, reviews and disputes are stored in whatever language the ticket was written in.

**Working language: English.**

- Every prompt asks for English. This covers plans, criteria, findings, disputes, executor summaries and the rules digest.
- Everything in `FRIDAY_HOME` is stored in English, so one agent always reads another agent's output in English.
- A ticket or brief in another language is still read as input; the planner restates it in English in `brief`.
- Owner decisions (`friday decide`) are stored as typed and also translated to English, and agents receive the English text.
- Commit messages and code comments follow the project's own rules (`engineering_policy`), defaulting to English. They are part of the repository, not output for the user.

**Two kinds of human-readable output.**

1. **Fixed text** (CLI help, `status`, `doctor`, errors, headings and labels in the MR and the journal) comes from catalogs. No model is involved.
2. **Text written by agents** (the plan in `friday plan`, findings and disputes, milestone summaries, the MR body, journal entries) goes through a new **`translate` role**.
   - It uses an inexpensive model of the `efficient` tier and translates a structured result field by field, keeping code, paths and identifiers as they are.
   - The terminology comes from `docs/<lang>/TERMS.md`.
   - Results are cached by text hash and language, so `status` and repeated runs of `plan` cost nothing.
   - With English selected, the step is skipped.
   - If translation fails, the English original is shown with a note; the pipeline never stops because of it.

**Choosing the language,** first match wins:

1. `--lang ru`;
2. `FRIDAY_LANG`;
3. the saved choice from `friday config language ru`, stored in `FRIDAY_HOME/config.json`;
4. the system locale (`LC_ALL`, `LC_MESSAGES`, `LANG`);
5. English.

For a team, the profile can pin `language` for what others read: the MR body and the shared journal. Without it, these follow the user's language.

**Catalogs:** `friday/locales/<lang>.json` (flat `key → text` pairs with `{name}` placeholders, standard library only).

- `en.json` is canonical; a missing key falls back to English.
- Errors are looked up by their `FridayError` code.
- Same ten languages as the docs: en, ru, zh-cn, ja, ko, pt-br, es, de, fr, id.
- English and Russian come straight from today's strings; the other eight are translated by an agent against TERMS.
- A test requires every catalog to have exactly the keys and placeholders of `en.json`. Tests pin `FRIDAY_LANG=en`.
- argparse's own boilerplate ("usage:", "options:") is overridden for the few strings Friday shows.

**Size.**

- About 173 Russian strings and 206 `FridayError` calls move to catalogs (errors on the common paths first, the rest with English fallback).
- 8 prompts change.
- The `translate` role and its cache are new, in a module of their own. Each model catalog in `examples/` gains the role.
- About 8 test files assert Russian text and need updating.

### Tier 2: blocks a core feature for repositories that are not Node

| Where | What is hardcoded | Replacement |
| --- | --- | --- |
| `gate.py:121-130` | `gate_node_modules` / `gate_tmpfs` only accept caches under `node_modules` | Generic `gate_mounts` (an image path linked into the snapshot) and `gate_tmpfs` (any absolute path), with the same safety rules: absolute paths, never inside the snapshot. Old keys are accepted as aliases |
| `Dockerfile.test` | Creates `/app/node_modules/.vite-temp` | Only needed by the preview tests, so documented as such or dropped |
| `tracker.py` | Yandex Tracker only (API or MCP); without a tracker, `--brief` is required | A small backend interface: `yandex` (existing), plus `github` (`gh issue view --json`, since delivery already uses `gh`). GitLab and Jira become extension points |

### Tier 3: browser acceptance against a staging site

The code already has a generic path. `preview.backend: "docker-local"` (`local_preview.py`) runs any `command` on any `port` in a networkless container, with `browser.mode: "local"`. It contains nothing specific to one project.

The project-specific part is the **staging** mode, which borrows an authenticated session from Chrome:

- `isolated_preview.py:68-104`: backend `docker-vite` hardcodes `/app/node_modules/vite/bin/vite.js`, port 5173, `VITE_API_APOLLO_URI` and `VITE_APP_ENV`.
- `browser_auth.py:25` and `browser_session.py:84` confirm the preview is wired to staging by parsing `VITE_API_APOLLO_URI` out of page scripts. They also copy a token from `localStorage`, which is one particular authentication scheme.
- `browser_session.py:41` and `browser_auth.py:83` **only accept staging hosts that match `*.stage.*`**. One company's naming convention is doing the job of a security allowlist. The replacement must keep the security property: exactly one explicit HTTPS origin from the profile, with no path, credentials, query or non-443 port. It must not become a looser regex.
- `browser_probe.py:33`: Chrome is expected at `127.0.0.1:9333`. Make this a profile or environment setting with that default.

Generalising means:

- one `docker-staging` backend with `command`, `port` and `env`, whose values may contain `{stage_origin}`;
- a readiness path;
- `auth` as a named strategy, with `local-storage-token` being today's only one;
- `docker-vite` kept as an alias that expands to today's values.

This touches about 20 places in tests and `docs/guide.md`.

### Extension points, not designed now

- Agent drivers beyond `claude` and `codex` (`runner.DRIVERS`, `models.DRIVERS`).
- `friday init`, which would detect the project's stack and write a starting profile.
- Windows: `fcntl` and the sandboxes are POSIX-only; WSL can be documented.

## 7. Coordination with the translation session

The docs-i18n session owns translation and the language file layout, and has no commits yet. Overlaps:

- `README.md`: the quickstart and development sections would change (packaging, HTTPS clone, a working example). Changes are limited to commands and process; prose stays theirs.
- `CONTRIBUTING.md`: would be rewritten as a contributor guide. Its language and translations are theirs to decide.
- `docs/guide.md` § Installation: install steps would change after packaging. Not edited here; flagged for them.
- **CLI strings** (argparse help in `bin/friday`, `doctor.render`, status output) are Russian while errors are English. Neither session owns them yet. This is an owner decision: English-only CLI output, or localising it.

## 8. Plan (approved 2026-11-03, implemented on branch claude/oss-readiness-f00e73)

Universality comes first, then everything around it. Each step runs the test suite.

1. **Language.** Agent prompts switch to English, along with the stored artifacts and English copies of owner decisions; the hint at `plan.py:69` goes. Add catalogs in `friday/locales/` for fixed text, the language choice (`--lang`, `FRIDAY_LANG`, `friday config language`, system locale, profile `language` for the MR and journal), and the `translate` role with a cache for text written by agents. English and Russian come first, then the other eight languages, with a test that the catalogs are complete.
2. **Model endpoints from the catalog.** Add `base_url` to catalog entries and keep `zai` as a built-in default.
3. **Generic gate mounts.** Add `gate_mounts` and generic `gate_tmpfs`, with the old keys kept as aliases.
4. **Tracker interface.** Keep the Yandex backend and add a GitHub Issues backend.
5. **Packaging.** Add `pyproject.toml` with the `friday` command and make `bin/friday` a shim. Move `browser_mcp.py` into the package and ship the templates as package data. Keep the version in one place and remove the `~/Projects` fallback from `SKILL.md`.
6. **Working examples and quickstart.** Add a Claude-only catalog and Node, Python and generic profiles. Use an HTTPS clone and `uv tool install` in the README, settle where profiles live after installation, and run `doctor` on a clean machine.
7. **Staging browser mode (if the owner places it before going public).** Add the `docker-staging` backend, an explicit staging origin in place of the `*.stage.*` check, `auth` strategies and a configurable Chrome URL, with `docker-vite` kept as an alias.
8. **CI.** Python 3.11–3.14 on Linux and macOS, ruff, least-privilege permissions, actions pinned by SHA, Dependabot.
9. **Community files.** CONTRIBUTING in English, CODE_OF_CONDUCT, SECURITY, CHANGELOG, issue forms and a PR template.
10. **Developer docs.** `docs/architecture.md`, an ADR README and template, and an ADR that records the universality decisions.
11. **Release process.** Write `RELEASING.md`; add PyPI Trusted Publishing only if publishing is chosen.
12. **Verify.** Run the tests on 3.11 and 3.13, run ruff, do a clean `uv tool install`, and run `doctor` against each example.

### Owner decisions (defaults in bold)

1. License: **MIT, with the copyright in the owner's name**.
2. Minimum Python: **3.11**, or keep 3.9.
3. Distribution name: **`friday-pipeline`**. PyPI: **not yet; install from git**.
4. Versioning: **SemVer 0.x, 0.3.0 for the packaged release, Keep a Changelog, git tags**.
5. Private material: **`profiles/` stays private; the owner rereads the pilot write-up before the repository goes public**.
6. Language (the owner's request): **agents work in English; people read the chosen language**. Open sub-questions: the model for `translate` (**the cheapest `efficient` model in the catalog**), and the commit message language (**the project's rules, default English**).
7. **Contributor Covenant 3.0 and GitHub private vulnerability reporting**. The owner turns that setting on.
8. Renamed profile keys: **old keys remain as aliases for one release**, or break now and document the change.
9. Staging browser mode (step 7): **before going public**, or document that it currently assumes Vite + Apollo.
10. The eight other languages: **translated by an agent against `TERMS.md` in the first release**, or English and Russian only at first.
