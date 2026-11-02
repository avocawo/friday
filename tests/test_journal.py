import json
import tempfile
import unittest
from pathlib import Path

from friday import card, journal
from friday.errors import FridayError

PLAN = {"summary": "Кнопка приветствия. Показывает имя.", "rules": [], "assumptions": ["Язык — русский."], "questions": [],
        "milestones": [{"id": "M1", "title": "Кнопка", "goal": "Кнопка на странице.", "stages": []},
                       {"id": "M2", "title": "Имя", "goal": "Имя в приветствии.", "stages": []}]}


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root, self.vault, self.repo = base / "home", base / "vault", base / "repo"
        (self.repo / ".friday").mkdir(parents=True)
        self.profile_path = base / "profile.json"
        self.profile_path.write_text(json.dumps({"name": "demo", "repository": str(self.repo),
                                                 "journal": [{"kind": "markdown", "dir": "vault"}, {"kind": "jsonl", "dir": "events"}]}))
        card.create(self.root, "T-1", str(self.profile_path))
        (card.directory(self.root, "T-1") / "plan.json").write_text(json.dumps(PLAN))
        def fill(value):
            value.update(state="running", branch="friday/T-1",
                         milestones=[{"id": "M1", "state": "done", "summary": "Добавил кнопку и тест.", "executor": "glm-5.3",
                                      "stages": [{"id": "M1.1", "commit": "abcdef1234567"}],
                                      "minor_findings": [{"file": "page.html", "problem": "Отступы."}], "outside_files": ["notes.md"]},
                                     {"id": "M2", "state": "pending", "stages": []}],
                         disputes=[{"milestone": "M1", "file": "page.html", "problem": "Нужен компонент.", "reviewer": "claude-opus-5-5",
                                    "executor_reason": "В проекте так не делают."}],
                         decisions=[{"name": "names", "text": "Пустое имя — ошибка.", "reopen": ["M2"]}])
            value["plan"].update(review_notes=[{"milestone": "M1", "stage": "M1.1", "problem": "Шаблон.", "answer": "Шаблонов нет."}])
        card.update(self.root, "T-1", fill)
        card.record(self.root, "T-1", "run_finished", role="develop", model="glm-5.3", milestone="M1", status="ok",
                    usage={"input": 1000, "output": 500, "cost_usd": 0.12}, duration_ms=60000)
        card.record(self.root, "T-1", "run_finished", role="review", model="claude-opus-5-5", milestone="M1", status="quota",
                    error="usage limit", usage={})

    def page(self):
        return (self.root.parent / "vault" / "T-1.md").read_text()

    def test_markdown_page_and_event_export(self):
        written = journal.sync(self.root, "T-1")
        self.assertEqual(len(written), 2)
        text = self.page()
        self.assertTrue(text.startswith("---\nfriday_generated: T-1\nkey: T-1\nstate: running"))  # front matter stays first
        for expected in ("# T-1 — Кнопка приветствия", "В работе · ветка `friday/T-1`", "### M1. Кнопка — готова",
                         "> Исполнитель (glm-5.3): Добавил кнопку и тест.", "- M1.1 `abcdef1234`", "Мелкое: `page.html` Отступы.",
                         "Вне плана: `notes.md`", "### M2. Имя — впереди", "Нужен компонент.", "В проекте так не делают.",
                         "**names**: Пустое имя — ошибка. (переоткрыты: M2)", "Несогласованное замечание (M1/M1.1): Шаблон.",
                         "| исполнение | 1 | 1k | $0.12 |", "review (claude-opus-5-5): quota usage limit", "Допущение: Язык — русский."):
            self.assertIn(expected, text)
        now = (self.root.parent / "vault" / "Friday — сейчас.md").read_text()
        self.assertIn("| [[T-1]] | В работе |", now)
        lines = (self.root.parent / "events" / "T-1.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(lines[0])["task"], "T-1")
        self.assertEqual(len(lines), len(card.events(self.root, "T-1")))

    def test_owner_notes_survive_and_foreign_pages_are_left_alone(self):
        journal.sync(self.root, "T-1")
        page = self.root.parent / "vault" / "T-1.md"
        page.write_text(self.page().replace("<!-- friday:notes -->\n", "<!-- friday:notes -->\nПроверить на iPhone.\n"))
        journal.sync(self.root, "T-1")
        self.assertIn("<!-- friday:notes -->\nПроверить на iPhone.\n<!-- /friday:notes -->", self.page())
        page.write_text("# Мои заметки о задаче\n")
        with self.assertRaises(FridayError) as error:
            journal.sync(self.root, "T-1")
        self.assertEqual(error.exception.code, "journal_conflict")
        card.record(self.root, "T-1", "milestone_done", milestone="M1")  # the pipeline goes on regardless
        self.assertEqual(page.read_text(), "# Мои заметки о задаче\n")

    def test_significant_events_update_the_journal_and_project_templates_win(self):
        (self.repo / ".friday" / "task.md").write_text("# {{key}}\n{{status}}\n{{disputes}}\n")
        card.record(self.root, "T-1", "gate", milestone="M1", status="passed")
        self.assertFalse((self.root.parent / "vault" / "T-1.md").exists())  # not significant
        card.record(self.root, "T-1", "milestone_done", milestone="M1", rounds=1, commits=1, disputes=1)
        text = self.page()
        self.assertTrue(text.startswith("<!-- friday:generated T-1 -->\n# T-1\nВ работе"))
        self.assertIn("Нужен компонент.", text)
        self.assertNotIn("## Расход", text)

    def test_pages_of_the_previous_friday_are_still_recognised(self):
        page = self.root.parent / "vault" / "T-1.md"
        page.parent.mkdir(parents=True)
        page.write_text("<!-- friday:generated T-1 -->\n# T-1\nold\n")
        journal.sync(self.root, "T-1")
        self.assertNotIn("old", self.page())

    def test_journal_dir_is_short_for_one_markdown_journal(self):
        self.assertEqual(journal.sinks({"journal_dir": "notes", "path": "/p/profile.json"}),
                         [{"kind": "markdown", "dir": "/p/notes"}])
        self.assertEqual(journal.sinks({}), [])
        with self.assertRaises(FridayError):
            journal.sinks({"journal": [{"kind": "slack", "dir": "x"}]})


if __name__ == "__main__":
    unittest.main()
