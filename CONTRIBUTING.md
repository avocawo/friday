# Contributing to Friday

Thanks for helping. This page covers setting up, the rules the code follows, and how a change gets merged. By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Report security problems privately, as described in [SECURITY.md](SECURITY.md).

## Set up

You need Python 3.11 or newer and Git. Friday has no runtime dependencies.

```sh
git clone https://github.com/avocawo/friday.git
cd friday
python3 -m unittest discover -s tests      # about a minute, no network or Docker needed
./bin/friday --help                         # run the CLI from the checkout
```

To use your checkout as your installed `friday`, install it in editable mode: `uv tool install --editable .` or `pipx install --editable .`.

Optional tools: [ruff](https://docs.astral.sh/ruff/) for linting (`uvx ruff check .`), and Docker for the hermetic test run (`docker build -f Dockerfile.test -t friday-test:local . && ./bin/test-offline`).

Read [docs/architecture.md](docs/architecture.md) first: it maps the pipeline to modules and shows where state lives. Terms such as milestone, stage, gate and blind check are defined in the [glossary](GLOSSARY.md).

## Rules for the code

- **The core stays project-independent.** Behaviour that one project needs belongs in its profile, not in Friday. If you need a new profile key, document it in the guide and give it a safe default.
- **Standard library only.** A runtime dependency needs a strong reason, discussed in an issue first.
- **Python 3.11+** syntax is fine; CI runs 3.11 to 3.14 on Linux and macOS.
- **Agents work in English.** Prompts ask for English, and stored artifacts are English. Do not add "write in the user's language" to a prompt.
- **Every message a person sees is translatable.** Wrap it in `_()` (or `N_()` where it is defined and `_()` where it is shown); give `FridayError` an English template with parameters: `FridayError("not_found", "Task {key} has no plan yet.", key=key)`. Then run `python -m friday.i18n`, which lists what each catalog in `friday/locales/` is missing, and add the translations. If you cannot translate a language, say so in the pull request and a maintainer will help; the build fails while a catalog is incomplete.
- **Keep the boundaries.** The executor's sandbox, the networkless gate and preview containers, the browser allowlist and the rule that secrets never reach prompts or logs are listed in the architecture page. A change near one of them needs a test that proves it still holds.
- **Tests come with the change.** Tests use temporary directories and the fake agents in `tests/test_runner.py`; they must not touch real projects, the network or your `FRIDAY_HOME`. A change to a command is tested by running the command as a process, checking its exit code and output.
- **Comments say why**, not what. Match the style of the surrounding code.

## Make a change

1. For anything bigger than a fix, open an issue first so we can agree on the approach. A change to how the pipeline is designed needs an [ADR](docs/adr/README.md).
2. Branch from `main`. Keep a pull request to one purpose.
3. Run `python3 -m unittest discover -s tests`, `uvx ruff check .` and `python3 -m friday.i18n`.
4. Update the docs that describe what you changed: README for commands, `docs/guide.md` for profile keys and behaviour. Add a line under "Unreleased" in [CHANGELOG.md](CHANGELOG.md).
5. Write commit messages as a short imperative sentence that says what the change does ("Read GitHub issues as tickets"), with details in the body when needed.
6. Open the pull request and fill in the template. CI must pass; a maintainer reviews it.

The documentation is translated into several languages under `docs/<lang>/`. Change the English text; translations are refreshed after the English lands.

## Releases

Maintainers release as described in [RELEASING.md](RELEASING.md).
