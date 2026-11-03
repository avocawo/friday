import importlib.machinery
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "docs-i18n"
loader = importlib.machinery.SourceFileLoader("docs_i18n", str(SCRIPT))
spec = importlib.util.spec_from_loader("docs_i18n", loader)
docs_i18n = importlib.util.module_from_spec(spec)
loader.exec_module(docs_i18n)

SOURCES = {
    "README.md": "# Demo\n\n**English**\n\nRun `friday status`. See the [guide]({guide}).\n\n"
                 "```sh\n# {comment}\nfriday status\n```\n",
    "docs/guide.md": "# Guide\n\n**English**\n\nUse `start`, then [the glossary]({glossary}).\n",
    "GLOSSARY.md": "# Terms\n\n**English**\n\n**Gate**: checks. Back to [README]({readme}).\n",
}
EN_LINKS = {"guide": "docs/guide.md", "glossary": "../GLOSSARY.md", "readme": "README.md", "comment": "comment"}
TRANSLATED_LINKS = {"guide": "guide.md", "glossary": "GLOSSARY.md", "readme": "README.md", "comment": "kommentar"}


class DocsI18nTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for source, text in SOURCES.items():
            self.write(source, text.format(**EN_LINKS))
            for lang, _ in docs_i18n.LANGUAGES[1:]:
                self.write(docs_i18n.translation_path(source, lang), text.format(**TRANSLATED_LINKS).replace("# ", "# T ", 1))
        docs_i18n.switchers(self.root)
        for source, lang, path in docs_i18n.documents():
            if lang != "en":
                docs_i18n.stamp(self.root, path)

    def write(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")

    def edit(self, path, old, new):
        file = self.root / path
        file.write_text(file.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    def test_faithful_translations_pass(self):
        self.assertEqual(docs_i18n.lint(self.root), [])
        self.assertEqual(docs_i18n.status(self.root), ([], []))

    def test_translated_inline_code_is_reported(self):
        self.edit("docs/ja/README.md", "`friday status`", "`friday 状態`")
        self.assertEqual(len(docs_i18n.lint(self.root)), 1)
        self.assertIn("docs/ja/README.md: inline code differs", docs_i18n.lint(self.root)[0])

    def test_changed_code_block_is_reported_but_comments_may_be_translated(self):
        self.edit("docs/de/README.md", "friday status\n```", "friday stop\n```")
        self.assertIn("docs/de/README.md: code blocks differ from README.md", docs_i18n.lint(self.root))

    def test_link_must_point_to_the_same_language(self):
        self.edit("docs/fr/README.md", "(guide.md)", "(../guide.md)")
        problems = docs_i18n.lint(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("docs/fr/README.md: links differ", problems[0])

    def test_broken_link_and_missing_switcher_are_reported(self):
        self.edit("docs/guide.md", "(../GLOSSARY.md)", "(../NOPE.md)")
        self.edit("docs/es/GLOSSARY.md", "**Español**", "Español")
        problems = docs_i18n.lint(self.root)
        self.assertIn("docs/guide.md: broken link ../NOPE.md", problems)
        self.assertTrue(any(p.startswith("docs/es/GLOSSARY.md: needs exactly one switcher line") for p in problems))

    def test_missing_marker_is_reported(self):
        self.edit("docs/id/guide.md", "<!-- i18n: source=docs/guide.md", "<!-- source=docs/guide.md")
        self.assertTrue(any(p.startswith("docs/id/guide.md: first line must be") for p in docs_i18n.lint(self.root)))

    def test_source_edit_marks_translations_stale_until_stamped(self):
        self.edit("GLOSSARY.md", "checks.", "deterministic checks.")
        missing, stale = docs_i18n.status(self.root)
        self.assertEqual(missing, [])
        self.assertEqual(stale, [docs_i18n.translation_path("GLOSSARY.md", lang) for lang, _ in docs_i18n.LANGUAGES[1:]])
        docs_i18n.stamp(self.root, "docs/ru/GLOSSARY.md")
        self.assertNotIn("docs/ru/GLOSSARY.md", docs_i18n.status(self.root)[1])

    def test_switcher_changes_do_not_mark_translations_stale(self):
        self.edit("README.md", "**English** · ", "**English** ·  ")
        self.assertEqual(docs_i18n.status(self.root), ([], []))

    def test_diff_shows_source_changes_since_translation(self):
        git = ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "."], check=True)
        subprocess.run(git + ["commit", "-qm", "docs"], check=True)
        self.edit("docs/guide.md", "Use `start`", "Use `start` first")
        subprocess.run(git + ["commit", "-qam", "edit"], check=True)
        result = subprocess.run([str(SCRIPT), "--root", str(self.root), "diff", "docs/ko/guide.md"],
                                check=True, capture_output=True, text=True)
        self.assertIn("+Use `start` first", result.stdout)

    def test_check_warns_unless_strict(self):
        (self.root / "docs/ru/README.md").unlink()
        command = [str(SCRIPT), "--root", str(self.root), "check"]
        relaxed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(relaxed.returncode, 0)
        self.assertIn("missing: docs/ru/README.md", relaxed.stdout)
        self.assertEqual(subprocess.run(command + ["--strict"], capture_output=True).returncode, 1)

    def test_repository_translations_pass_lint(self):
        result = subprocess.run([str(SCRIPT), "lint"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
