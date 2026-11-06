# Research: translating Friday's documentation

Date: 2026-11-03. Question: how to publish Friday's user documentation in ten languages with English as the canonical source, and how to keep the translations in sync. Sources are listed at the end; claims that rely on one are marked with its number.

## Summary

- Ten languages, English included: English, Simplified Chinese, Japanese, Korean, Brazilian Portuguese, Spanish, Russian, German, French, and Indonesian. The first nine are the exact set GitHub Docs ships [1]. All nine are also maintained in the FastAPI docs [3] and in production in the Python docs [5]. The tenth, Indonesian, is the weakest pick, and Turkish is the alternative.
- Translate four files: `README.md`, `docs/guide.md`, `GLOSSARY.md`, and, once the contributor-process work settles it, `CONTRIBUTING.md`. Everything else stays as it is.
- Use `docs/<lang>/` folders and plain Markdown on GitHub. A docs site generator is not worth it for four files.
- Sync: every translated file starts with an HTML comment that records its source file and a hash of that source's content. A structural test runs in `tests/` and fails on broken translations. A separate script reports stale ones but does not fail the build.
- Terminology: one canonical English glossary that matches code identifiers, plus one term sheet per language. The term sheet doubles as the translator prompt, a pattern FastAPI uses [4].
- Friday's own CLI output (`--help`, messages, journal templates) is out of scope. It currently mixes Russian and English, which contradicts English documentation, so it needs a separate change.

## 1. Which ten languages

What matters is the developer audience, not the number of speakers. The best evidence of developer demand is which languages large developer-facing projects choose to maintain, because they pay the upkeep cost.

| Source | Languages besides English |
| --- | --- |
| GitHub Docs [1] | es, ja, pt (Brazil), zh (Simplified), ru, fr, ko, de |
| FastAPI docs [3] | de, es, fr, hi, ja, ko, pt, ru, tr, uk, zh, zh-hant |
| Python docs, in production [5] | de, el, es, fr, it, ja, ko, pa, pl, pt_BR, fa, ro, ru, tr, zh_CN, zh_TW, and more (id, sv, uk not yet in production) |

All three sources share the same nine: **en, zh-CN, ja, ko, pt-BR, es, ru, de, fr**. Octoverse 2025 [2] supports the set by country: the US, India, China, Brazil, the UK, Japan, Germany, Indonesia, Russia, and Canada are the top ten developer populations. Brazil grew from about 6.9 million to a projected 19.6 million by 2030, and Japan is projected to reach 11.7 million.

Variants: `pt-BR` rather than European Portuguese, because Brazil is the audience and GitHub labels it "Português do Brasil" [1]. `zh-CN` (Simplified) rather than Traditional, because China is the top-ten country.

The tenth language is a judgment call:

- **Indonesian (`id`), recommended.** Indonesia is in the Octoverse top ten (4.37 million) and among the fastest-growing countries since 2020 [2]. Against it, Python marks `id` as not yet in production [5], which suggests a thin pool of volunteer translators. That matters less here because the translations are model-made.
- **Turkish (`tr`), alternative.** It has a strong translator community (FastAPI, and Python in production), but Turkey is not in the Octoverse top ten.
- **Hindi (`hi`), not recommended.** India is the second-largest population, but its developers overwhelmingly read technical English. FastAPI's Hindi docs are the newest and thinnest of its set.
- Ukrainian and Traditional Chinese: smaller reach than the above.

Russian is required by the owner. It is also the only language with a native reviewer on hand.

## 2. Which documents

| File | Decision | Why |
| --- | --- | --- |
| `README.md` | English rewrite + 9 translations | First thing every visitor reads |
| `docs/guide.md` | English rewrite + 9 translations | The only user manual |
| `GLOSSARY.md` | English rewrite + 9 translations | Defines the terms the other two use; OpenTelemetry translates its glossary second, right after the home page, so terminology is fixed before other pages [6] |
| `CONTRIBUTING.md` | English, written by the contributor-process session; translate last, once it is stable | Contributors work in English in issues and PRs anyway, and the file is still changing |
| `examples/*.json` | Leave as is | Already English, no prose |
| `plugins/friday/skills/friday/SKILL.md` | Leave as is | Agent-facing and already English |
| `docs/adr/`, `docs/research/`, `docs/research.md`, `docs/process-redesign.md` | Leave untranslated | Design records, not user docs. They are in Russian, which is odd for a public repository; translating them to English is a separate follow-up |
| `friday/templates/*.md` | Out of scope | Program output, see section 6 |

Every current user document is in Russian, so the English canonical text is a rewrite rather than a copy. The existing Russian text becomes the starting point of the Russian translation: revise it to match the English structure instead of translating it back from English.

## 3. Layout and language switcher

Options seen in practice:

- `README.<lang>.md` at the repository root (for example GitHub spec-kit's `README.zh-CN.md` [7]). This works for a single README but puts nine extra files at the root and does not cover the guide.
- `docs/<lang>/` trees, the layout used by FastAPI (`docs/en`, `docs/ru`, …) [3].
- A generator's own tree, such as Docusaurus `i18n/<locale>/docusaurus-plugin-content-docs/current/` [8]. That needs a Node build and hosting.

Recommendation: keep English where it is (`README.md`, `docs/guide.md`, `GLOSSARY.md`) and put translations in `docs/<lang>/README.md`, `docs/<lang>/guide.md`, and `docs/<lang>/GLOSSARY.md`, with lowercase BCP 47 folder names (`zh-cn`, `pt-br`, `ja`, …). GitHub renders relative links between Markdown files and resolves them on the current branch [9], so plain Markdown is enough. No site generator: four files per language do not justify a build, hosting, and a second toolchain. Revisit this if the guide grows into many pages.

Switcher: one line under the title of every file, languages named in their own language ("Русский", not "Russian"), current language as plain text, no flags. This follows the W3C advice to label language options in the target language [10].

```markdown
**English** · [Русский](docs/ru/README.md) · [简体中文](docs/zh-cn/README.md) · [日本語](docs/ja/README.md) · …
```

## 4. Keeping translations in sync

How established projects detect stale translations:

- **MDN** records `l10n.sourceCommit`, the hash of the last English commit that a translation follows, in each translated page's front matter [11].
- **OpenTelemetry** records `default_lang_commit` in front matter. `npm run check:i18n` lists drifted files, and `check:i18n -- diff <path>` shows what changed in English [6].
- **FastAPI** compares the last commit date of the English file with that of the translation and calls the translation outdated when English is newer. Its `scripts/translate.py` then re-translates with an LLM, feeding it the previous translation so unchanged parts stay put [4].
- **Kubernetes** tracks stale localized pages as issues that link a diff against the English text [12]. Any of these signals also fires on cosmetic English edits, so a drift report still needs a human to judge it.

For Friday, record a **content hash** instead of a commit hash, in an HTML comment on the first line:

```markdown
<!-- i18n: source=docs/guide.md sha256=3f2a9c1b07e4 -->
```

- A commit hash cannot refer to the commit that is being made, so English and its translations could never land in one commit. A squash merge also discards intermediate commits and breaks their hashes. A content hash has neither problem and works in an uncommitted tree.
- YAML front matter would render as a table at the top of the file on GitHub. An HTML comment stays invisible.
- To see what changed, a script finds the last commit in which the English file had the recorded hash and diffs from there.

Two checks:

1. **A structural test in `tests/`** runs with the rest of the suite and fails on: a missing or malformed marker; a missing language file; a broken relative link; fenced code blocks or inline code that differ from the English source; or a different number of headings. Equal code spans catch the most common machine-translation damage: translated commands, keys, or paths.
2. **A staleness report, `bin/docs-i18n check`**, lists translations whose recorded hash no longer matches English and prints the English diff since then. It warns but does not fail. If it failed the build, every English edit would be blocked until nine translations caught up, which is hostile to outside contributors. Translations are refreshed in batches, before a release or when the report grows.

Each translated file also carries one line noting that it is a translation and that the English text governs.

## 5. Terminology

The English glossary uses the same words as the code and profile keys: `task`, `plan`, `milestone`, `stage`, `criterion`, `owner`, `planner`, `executor`, `reviewer`, `model tier`, `gate`, `card`, `journal`, `disputed finding`, `profile`. The grep counts on `friday/*.py` were milestone 106, stage 74, card 131, executor 26, dispute 42, journal 11. `profile` is missing from the current glossary and should be added.

Each language gets a term sheet, `docs/<lang>/TERMS.md`: tone, the English term, the chosen translation, and whether the English term stays in parentheses. It is FastAPI's per-language `llm-prompt.md` [4] in table form, and it serves as the translator prompt. The Russian sheet takes its terms from the current Russian `GLOSSARY.md` (веха, этап, гейт, карточка, …).

Rules, following OpenTelemetry [6]: never translate code, commands, profile keys, file names, or link targets. Translate prose and headings.

## 6. CLI text

Translating Friday's own messages is out of scope. Doing it would mean a message catalogue (gettext), a locale setting, and translated test expectations, and that is a different project.

The current state does conflict with English docs, though. `bin/friday` `--help` text and status messages are in Russian, while errors raised by `FridayError` are in English. `friday/status.py`, `journal.py`, `plan.py`, `models.py`, and `final.py` produce Russian strings, and so do the journal templates `friday/templates/now.md` and `task.md`. Switching all program output to English is a separate code change that should precede or accompany the English docs.

## Sources

1. GitHub Docs, language list: https://github.com/github/docs/blob/main/src/languages/lib/languages.ts
2. GitHub Octoverse 2025: https://github.blog/news-insights/octoverse/octoverse-a-new-developer-joins-github-every-second-as-ai-leads-typescript-to-1/
3. FastAPI docs languages (`docs/` tree): https://github.com/fastapi/fastapi/tree/master/docs
4. FastAPI translation tooling: https://github.com/fastapi/fastapi/blob/master/scripts/translate.py and https://github.com/fastapi/fastapi/blob/master/docs/ru/llm-prompt.md
5. Python docs build configuration: https://github.com/python/docsbuild-scripts/blob/main/config.toml
6. OpenTelemetry, site localization: https://opentelemetry.io/docs/contributing/localization/
7. GitHub spec-kit, Simplified Chinese README: https://github.com/github/spec-kit/pull/3740
8. Docusaurus i18n tutorial: https://docusaurus.io/docs/i18n/tutorial
9. GitHub, relative links: https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#relative-links
10. W3C, language selection navigation: https://www.w3.org/International/questions/qa-navigation-select
11. MDN translated content, `sourceCommit` discussion: https://github.com/orgs/mdn/discussions/129
12. Kubernetes, stale Japanese page compared with English: https://github.com/kubernetes/website/issues/23617
