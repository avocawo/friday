# Changelog

All notable changes to Friday are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/): while the version is 0.x, a minor version may change commands and profile keys.

## [Unreleased]

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
