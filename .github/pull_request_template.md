## What and why

<!-- What changes and the reason. Link the issue it closes, e.g. "Closes #12". -->

## How it was checked

<!-- The tests you added or ran, and what you tried by hand. -->

- [ ] `python -m unittest discover -s tests` passes
- [ ] `ruff check .` passes
- [ ] New or changed messages are in every catalog (`python -m friday.i18n` prints `ok`)
- [ ] README, guide and `CHANGELOG.md` are updated if a command, profile key or behaviour changed
- [ ] An ADR is added in `docs/adr/` if this changes how the pipeline is designed
