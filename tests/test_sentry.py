import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import sentry, state


class SentryTests(unittest.TestCase):
    def test_scans_stack_frames_against_added_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repo / "app.py").write_text("old\n")
            git("add", "app.py")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (repo / "app.py").write_text("old\nnew\n")
            git("add", "app.py")
            git("commit", "-qm", "change")
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": str(repo), "sentry": {
                "base_url": "https://sentry.example", "organization": "org", "project": "app",
                "token_env": "TEST_SENTRY_TOKEN"}}))
            state.create(root, "T-1", str(config))
            state.update(root, "T-1", lambda card: card.update({"plan_base": base}))
            event = {"eventID": "event1", "title": "error", "entries": [{"data": {"frames": [
                {"filename": "/src/app.py", "lineNo": 2}, {"filename": "/src/app.py", "lineNo": 1}]}}]}
            with patch.dict(os.environ, {"TEST_SENTRY_TOKEN": "secret"}), \
                 patch("friday.sentry.http.read_json_page", return_value=([event], "")):
                result = sentry.scan(root, "T-1")
            self.assertEqual(result["matches"][0]["frames"], [{"file": "app.py", "line": 2}])
            self.assertFalse(result["truncated"])

    def test_poll_notifies_only_once_for_same_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": str(root)}))
            state.create(root, "T-1", str(config))
            result = {"head": "abc", "matches": [{"event": "event1", "frames": []}], "truncated": False}
            with patch("friday.sentry.scan", return_value=result), patch("friday.watch.notify") as notify:
                self.assertEqual(len(sentry.poll(root, "T-1")["matches"]), 1)
                self.assertEqual(sentry.poll(root, "T-1")["matches"], [])
                notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
