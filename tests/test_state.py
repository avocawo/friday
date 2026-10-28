import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import journal, state
from friday.errors import FridayError

CLI = Path(__file__).resolve().parents[1] / "bin" / "friday"


def add_events(root):
    for _ in range(20):
        state.update(root, "T-1", lambda card: card["events"].append({"type": "test"}))


class StateTests(unittest.TestCase):
    def test_concurrent_updates_do_not_lose_events(self):
        with tempfile.TemporaryDirectory() as directory:
            state.create(directory, "T-1", "/profile.json")
            workers = [multiprocessing.Process(target=add_events, args=(directory,)) for _ in range(4)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(10)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(len(state.read(directory, "T-1")["events"]), 80)

    def test_invalid_keys_and_corrupt_state_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FridayError):
                state.create(directory, "../escape", "/profile.json")
            state.create(directory, "T-1", "/profile.json")
            state.task_path(directory, "T-1").write_text("{", encoding="utf-8")
            with self.assertRaises(FridayError) as error:
                state.read(directory, "T-1")
            self.assertEqual(error.exception.code, "invalid_state")
            card = {"version": 1, "key": "T-1", "profile": "x", "status": "open", "stages": {"one": []}, "events": []}
            state.task_path(directory, "T-1").write_text(json.dumps(card), encoding="utf-8")
            with self.assertRaises(FridayError) as error:
                state.read(directory, "T-1")
            self.assertEqual(error.exception.code, "invalid_state")

    def test_cli_creates_task_and_generated_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": ".", "journal_dir": "notes"}))
            env = dict(os.environ, FRIDAY_HOME=str(root / "state"))
            def run(*args):
                return subprocess.run([sys.executable, str(CLI), *args], env=env, cwd=directory,
                                      capture_output=True, text=True)
            result = run("new", "T-1", "--profile", str(config))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["data"]["key"], "T-1")
            self.assertEqual(json.loads(result.stdout)["data"]["mode"], "auto")
            manual = run("new", "T-2", "--profile", str(config), "--manual")
            self.assertEqual(json.loads(manual.stdout)["data"]["mode"], "manual")
            self.assertTrue((root / "notes" / "T-1.md").is_file())
            self.assertEqual(json.loads(run("status", "T-1").stdout)["data"]["key"], "T-1")
            self.assertIn("T-1", [task["key"] for task in json.loads(run("today").stdout)["data"]])
            self.assertEqual(json.loads(run("new", "T-1", "--profile", str(config)).stderr)["error"]["code"],
                             "already_exists")

    def test_journal_does_not_overwrite_user_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": ".", "journal_dir": "notes"}))
            state.create(root, "T-1", str(config))
            notes = root / "notes"
            notes.mkdir()
            target = notes / "T-1.md"
            target.write_text("my own note", encoding="utf-8")
            with self.assertRaises(FridayError) as error:
                journal.sync(root, "T-1")
            self.assertEqual(error.exception.code, "journal_conflict")
            self.assertEqual(target.read_text(), "my own note")

    def test_journal_creation_does_not_clobber_concurrent_note(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": ".", "journal_dir": "notes"}))
            state.create(root, "T-1", str(config))
            target = root / "notes" / "T-1.md"
            real_link = os.link
            def concurrent_note(source, destination):
                target.write_text("my concurrent note")
                return real_link(source, destination)
            with patch("friday.journal.os.link", side_effect=concurrent_note):
                with self.assertRaises(FridayError) as error:
                    journal.sync(root, "T-1")
            self.assertEqual(error.exception.code, "journal_conflict")
            self.assertEqual(target.read_text(), "my concurrent note")

    def test_replacing_generated_journal_does_not_overwrite_new_note(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": ".", "journal_dir": "notes"}))
            state.create(root, "T-1", str(config))
            journal.sync(root, "T-1")
            target = root / "notes" / "T-1.md"
            real_open = os.open
            def replace_after_open(path, flags, *args, **kwargs):
                fd = real_open(path, flags, *args, **kwargs)
                if os.path.realpath(path) == str(target.resolve()):
                    target.rename(target.with_suffix(".old"))
                    target.write_text("my new note")
                return fd
            with patch("friday.journal.os.open", side_effect=replace_after_open):
                with self.assertRaises(FridayError) as error:
                    journal.sync(root, "T-1")
            self.assertEqual(error.exception.code, "journal_conflict")
            self.assertEqual(target.read_text(), "my new note")


if __name__ == "__main__":
    unittest.main()
