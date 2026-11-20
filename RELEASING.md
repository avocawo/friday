# Releasing Friday

1. Make sure `main` is green in CI and `python -m friday.i18n` prints `ok` for every language.
2. Choose the version by [Semantic Versioning](https://semver.org/). While Friday is 0.x, a change to a command or a profile key raises the minor version.
3. Set it in `friday/__init__.py` (`__version__`) and in both plugin manifests (`plugins/friday/.claude-plugin/plugin.json`, `plugins/friday/.codex-plugin/plugin.json`); the test suite checks they agree.
4. In `CHANGELOG.md`, rename "Unreleased" to the version and date and start a new empty "Unreleased".
5. Commit as "Release X.Y.Z", tag it `vX.Y.Z` (annotated), and push the commit and the tag.
6. Create a GitHub release from the tag with the changelog section as its notes.

People install a release with:

```sh
uv tool install git+https://github.com/avocawo/friday@vX.Y.Z
```

Friday is not published to PyPI yet. When it is, publishing will use PyPI Trusted Publishing from a GitHub Actions workflow on tags, so no API token is stored anywhere.
