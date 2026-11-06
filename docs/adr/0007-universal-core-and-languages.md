---
status: accepted
---

# A core without one project's stack; agents work in English, people read their own language

Friday began as one owner's tool for one frontend project. When it opened to other teams, three kinds of assumption were still built into the core: that project's stack (a Vite + Apollo preview, `node_modules` caches in the gate, a `*.stage.*` host naming convention, Chrome on port 9333, Z.ai as the only extra endpoint, Yandex Tracker as the only tracker), the owner's language (Russian in the CLI, the MR body and the journal), and agents writing in "the task's language", so that stored plans and reviews mixed languages.

Project-specific behaviour now lives in the profile, and the core keeps only what every project shares:

- **Models:** any Anthropic-compatible endpoint is a `base_url` on a catalog entry; `zai` is only a built-in default.
- **Gate:** `gate_links` (snapshot path → image path) and `gate_tmpfs` (any absolute path outside `/workspace`) replace the `node_modules`-only options. `gate_node_modules` remains as an alias.
- **Tracker:** `tracker.backend` chooses `yandex`, `yandex-mcp` or `github`; the next tracker is one more reader returning the same dictionary.
- **Preview:** `docker-staging` runs any dev server (`command`, `port`, `env` with `{stage_origin}`, `links`, `tmpfs`). `docker-vite` is an alias holding the old values and implies the old `stage_check`.
- **Staging origin:** it must be one explicit HTTPS origin whose host is named as a test environment (`stage`, `staging`, `dev`, `qa`, `preview`, …), or match `browser.stage_host_pattern`. This keeps the guarantee that a profile cannot point the browser at production by accident, without one company's naming. The Chrome endpoint is `browser.chrome_url` or `FRIDAY_CHROME_URL`.

**Agents work only in English.** Every prompt asks for English, and everything in `FRIDAY_HOME` is English. A ticket in another language is input; owner decisions are kept as written and given to agents in English. People read Friday in the language they choose (`--lang`, `FRIDAY_LANG`, `friday config language`, the system locale; the profile's `language` for the MR and the shared journal):

- fixed text comes from catalogs in `friday/locales/`, keyed by the English message;
- text written by agents goes through the `translate` role: the catalog's least expensive model, one structured run per batch, cached by text and language. A failed translation shows the English original and never stops the pipeline.

## Consequences

- Every user-visible message goes through `i18n`, and every catalog must have it; `python -m friday.i18n` and the test suite check this.
- Errors carry an English template and parameters, so logs stay English and the CLI shows them translated.
- Showing a plan or a journal in another language costs one inexpensive model run per new text.
- The owner's existing profiles keep working through the aliases for one release.

## Considered Options

- **English-only output.** Simplest, and it was the default proposal. Turned down by the owner: Friday is meant for teams who do not read English comfortably.
- **Agents write in the reader's language.** No translation step, but plans and reviews would mix languages, prompts would need a language each, and agents reading each other's output would work in a second language. Turned down.
- **GNU gettext catalogs.** The standard, but compiling `.po` to `.mo` needs a tool outside the standard library. JSON catalogs keyed by English text give the same fallback.
- **Keep the `*.stage.*` rule.** Safe but tied to one company's DNS. A list of test-environment labels with an override pattern keeps the safety.
