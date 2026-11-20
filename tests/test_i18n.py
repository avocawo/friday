import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from friday import card, i18n, translate
from friday.errors import FridayError

ROOT = Path(__file__).resolve().parents[1]


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.root = Path(self.home.name)
        self.addCleanup(self.home.cleanup)
        self.addCleanup(i18n.select, "en")

    def test_language_comes_from_flag_env_saved_choice_then_system(self):
        with patch.dict(os.environ, {"LANG": "de_DE.UTF-8"}, clear=False):
            os.environ.pop("FRIDAY_LANG", None)
            os.environ.pop("LC_ALL", None)
            os.environ.pop("LC_MESSAGES", None)
            self.assertEqual(i18n.resolve(None, self.root), "de")
            i18n.save("ja", self.root)
            self.assertEqual(i18n.resolve(None, self.root), "ja")
            with patch.dict(os.environ, {"FRIDAY_LANG": "pt_BR"}):
                self.assertEqual(i18n.resolve(None, self.root), "pt-br")
                self.assertEqual(i18n.resolve("zh_CN", self.root), "zh-cn")
            self.assertEqual(i18n.resolve("klingon", self.root), "en")
        self.assertEqual(i18n.output_language({"language": "fr"}), "fr")

    def test_every_catalog_matches_the_messages_in_the_code(self):
        found = i18n.messages()
        self.assertIn("Not ready: fix the items marked ✗.", found)
        for lang in i18n.LANGUAGES[1:]:
            self.assertEqual(i18n.check(lang, found), [], lang)

    def test_messages_and_errors_render_in_the_chosen_language(self):
        i18n.select("ru")
        self.assertEqual(i18n._("Not ready: fix the items marked ✗."), i18n.catalog("ru")["Not ready: fix the items marked ✗."])
        self.assertIn("3", i18n.ngettext("{n} run", "{n} runs", 3))
        error = FridayError("invalid_state", "Task {key} is already {state}.", key="T-1", state="running")
        self.assertEqual(str(error), "Task T-1 is already running.")  # stored and logged in English
        self.assertIn("T-1", i18n.error(error))
        self.assertNotEqual(i18n.error(error), str(error))
        i18n.select("en")
        self.assertEqual(i18n.ngettext("{n} run", "{n} runs", 1), "1 run")

    def test_cli_help_follows_lang(self):
        env = dict(os.environ, FRIDAY_HOME=str(self.root))
        english = subprocess.run([sys.executable, "-m", "friday", "--lang", "en", "--help"], capture_output=True, text=True, cwd=ROOT, env=env)
        russian = subprocess.run([sys.executable, "-m", "friday", "--lang", "ru", "--help"], capture_output=True, text=True, cwd=ROOT, env=env)
        self.assertIn("approve the plan", english.stdout)
        self.assertNotIn("approve the plan", russian.stdout)
        saved = subprocess.run([sys.executable, "-m", "friday", "config", "language", "ru"], capture_output=True, text=True, cwd=ROOT, env=env)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(json.loads((self.root / "config.json").read_text())["language"], "ru")


class TranslateTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.root = Path(self.home.name)
        self.addCleanup(self.home.cleanup)
        card.create(self.root, "T-1", str(self.root / "profile.json"))
        self.profile = {"models": {"small": {"model": "m-small", "vendor": "v", "driver": "claude", "tier": "efficient"},
                                   "big": {"model": "m-big", "vendor": "v", "driver": "claude", "tier": "frontier"}}}

    def fake(self, calls):
        def run(spec, on_event=None):
            calls.append(spec)
            items = json.loads(spec.prompt.split("\n\n", 1)[1])
            return SimpleNamespace(status="ok", structured={"translations": [{"id": item["id"], "text": "«" + item["text"] + "»"}
                                                                             for item in items]},
                                   usage={"input": 10, "output": 5}, duration_ms=5, error="", limits=None)
        return run

    def test_agent_text_is_translated_once_by_the_cheapest_model_and_cached(self):
        calls = []
        with patch("friday.translate.runner.run", side_effect=self.fake(calls)):
            first = translate.mapping(self.root, "T-1", self.profile, ["Add a button.", "src/app.py"], "ru")
            second = translate.mapping(self.root, "T-1", self.profile, ["Add a button."], "ru")
        self.assertEqual((first["Add a button."], second["Add a button."]), ("«Add a button.»", "«Add a button.»"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].model, "m-small")
        self.assertIn("Русский", calls[0].prompt)
        finished = [e for e in card.events(self.root, "T-1") if e["type"] == "run_finished"]
        self.assertEqual([e["role"] for e in finished], ["translate"])

    def test_english_needs_no_run_and_a_failure_shows_the_original(self):
        with patch("friday.translate.runner.run", side_effect=AssertionError("no run expected")):
            self.assertEqual(translate.mapping(self.root, "T-1", self.profile, ["Plain English."], "en"), {"Plain English.": "Plain English."})
        failed = SimpleNamespace(status="failed", structured=None, usage={}, duration_ms=1, error="boom", limits=None)
        with patch("friday.translate.runner.run", return_value=failed), patch("sys.stderr"):
            self.assertEqual(translate.mapping(self.root, "T-1", self.profile, ["Add a button."], "de"), {"Add a button.": "Add a button."})

    def test_owner_decisions_reach_agents_in_english(self):
        calls = []
        with patch("friday.translate.runner.run", side_effect=self.fake(calls)):
            english = translate.to_english(self.root, "T-1", self.profile, "Пустое имя — ошибка.")
        self.assertEqual(english, "«Пустое имя — ошибка.»")
        self.assertIn("into English", calls[0].prompt)
        decisions = [{"name": "names", "text": "Пустое имя — ошибка.", "text_en": english, "reopen": []}]
        self.assertEqual(card.for_agents(decisions)[0]["text"], english)


if __name__ == "__main__":
    unittest.main()
