# Changelog

All notable changes to Friday are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/): while the version is 0.x, a minor version may change commands and profile keys.

## [Unreleased]

### Added

- Analysis tasks: a profile with `"kind": "analysis"` runs the project's `analysis_command` in a project session, takes the owner's answers through `decide` and ends when `analysis_check` passes, without milestones or an MR (ADR 0017). `decide --hold` keeps an answer without a session turn, `friday reopen` continues finished analysis in the same session, `status` shows its documents, questions and cost, and the session may not write the owner's Claude Code memory.
- `decide --file` reads a long decision from a file; `status` shows what a running session has spent so far.
- Project toolset: `"toolset": {"plan": "project", "execute": "project"}` lets the project's own commands plan and run a task in an ordinary Claude Code session, while Friday keeps the gate, browser acceptance, final review and delivery (ADR 0015).
- `friday serve`: one queue of approved tasks per machine with `max_workers`, recovery of tasks whose process died and system notifications; `friday service print|install|uninstall|check` runs it as a launchd or systemd user service (ADR 0014).
- Profile steps (`steps`, `friday step`) for project procedures only the owner runs, such as building the gate image; `gate_image_step` stops a task for this step when the image was built for another lockfile (ADR 0011).
- `decide` revises a plan that awaits approval and reviews only the changed milestones; `friday plan KEY --diff` shows what changed (ADR 0016).
- Component showcase (`stories`), design tools (`design`: Figma, /impeccable), `executor_tiers`, `budget`, `max_parallel_milestones`, `final_review_roles`, `project_lessons`, `journal` `page`, `resume --brief`.

### Changed

- A milestone nothing checked is shown as "done, not verified" with its reasons (ADR 0009).
- Branches are named `<repository>/{key}` by default.
- The gate excerpt keeps the failure lines of a long section, failed TAP tests first.

## [0.3.0] - unreleased

### Added

- Friday can be installed as a package (`uv tool install`, `pipx install`) with a `friday` command; `python -m friday` works too.
- Friday speaks ten languages: `--lang`, `FRIDAY_LANG`, `friday config language`, or the system language. The profile's `language` sets the language of the MR and the shared journal. Agents always work in English; a new `translate` role, using the least expensive model in the catalog, translates what they wrote, with a cache.
- `base_url` on a model catalog entry connects any Anthropic-compatible endpoint.
- `tracker.backend: "github"` reads tasks from GitHub Issues through `gh`.
- `gate_links` and `gate_tmpfs` work for any stack (for example `.venv` from the image), and `gate_image_root` sets where the gate image keeps the lockfile.
- `preview.backend: "docker-staging"` runs any dev server against staging; `browser.stage_check`, `browser.stage_host_pattern` and `browser.chrome_url` configure what was fixed before.
- Example profiles for Node and Python projects and example gate images; a Claude-only model catalog that works with nothing but Claude Code.
- CI on Linux and macOS for Python 3.11–3.14, contributor guide, code of conduct, security policy, issue and PR templates, architecture overview and ADR process.

### Changed

- Prompts ask agents for English, and plans, reviews and summaries are stored in English. Owner decisions are kept as written and given to agents in English.
- CLI output, the MR body and the journal are in English by default instead of Russian.
- A staging origin must be named as a test environment (stage, staging, dev, qa, preview, …) instead of matching `*.stage.*`.
- The previous model catalog is now `examples/models-multi-vendor.json`.
- Python 3.11 or newer is required.

### Deprecated

- `gate_node_modules` (use `gate_links`), `preview.backend: "docker-vite"` (use `docker-staging`), `tracker.backend: "mcp"` (use `yandex-mcp`). They keep working in 0.3.

## [0.2.0]

- The milestone pipeline (ADR 0001–0006): plan and plan review, executor sessions per milestone, isolated gate, milestone review, browser acceptance, final review with a blind check, draft MR.
