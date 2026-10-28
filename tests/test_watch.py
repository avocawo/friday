import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import state, watch


class WatchTests(unittest.TestCase):
    def test_deployment_requires_association_with_exact_merge_request(self):
        config = {"base_url": "https://gitlab.example", "project": "group/repo",
                  "token_env": "TEST_GITLAB_TOKEN", "deployment_environment": "production"}
        mr = {"iid": 12, "state": "merged", "target_project_id": 42, "head_pipeline": None}
        deployments = [{"id": 7, "status": "success", "sha": "abc", "environment": {"name": "production"}}]
        with patch.dict(os.environ, {"TEST_GITLAB_TOKEN": "secret"}), \
             patch("friday.watch.http.read_json", side_effect=[mr, deployments, [{"iid": 12, "target_project_id": 42}]]):
            result = watch.fetch(config, 12)
        self.assertEqual(result["deployment"]["id"], 7)
        with patch.dict(os.environ, {"TEST_GITLAB_TOKEN": "secret"}), \
             patch("friday.watch.http.read_json", side_effect=[mr, deployments, [{"iid": 12, "target_project_id": 999}]]):
            result = watch.fetch(config, 12)
        self.assertNotIn("deployment", result)

    def test_only_meaningful_changes_are_notified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": ".", "gitlab": {}}))
            state.create(root, "T-1", str(config))
            watch.link(root, "T-1", 12)
            pending = {"state": "opened", "pipeline": "pending", "pipeline_sha": "a", "notes": 2}
            failed = dict(pending, pipeline="failed")
            with patch("friday.watch.fetch", side_effect=[pending, pending, failed, failed]), \
                 patch("friday.watch.notify") as notify:
                for _ in range(4):
                    watch.poll(root, "T-1")
                notify.assert_called_once_with("T-1", ["CI failed"])
            self.assertEqual(state.read(root, "T-1")["watch"], failed)

    def test_notification_failure_does_not_consume_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": ".", "gitlab": {}}))
            state.create(root, "T-1", str(config))
            watch.link(root, "T-1", 12)
            failed = {"state": "opened", "pipeline": "failed", "pipeline_sha": "a", "notes": 0}
            with patch("friday.watch.fetch", return_value=failed), patch("friday.watch.notify", side_effect=OSError("fail")):
                with self.assertRaises(OSError):
                    watch.poll(root, "T-1")
            self.assertNotIn("watch", state.read(root, "T-1"))

    def test_background_errors_notify_once_until_resolved(self):
        with tempfile.TemporaryDirectory() as directory:
            errors = {"T-1:gitlab": {"code": "credentials_missing", "message": "missing"}}
            with patch("friday.watch.notify") as notify:
                watch.remember_errors(directory, errors)
                watch.remember_errors(directory, errors)
                self.assertEqual(notify.call_count, 1)
                watch.remember_errors(directory, {})
                watch.remember_errors(directory, errors)
                self.assertEqual(notify.call_count, 2)

    def test_local_plan_approval_is_notified_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": "."}))
            state.create(root, "T-1", str(config))
            state.update(root, "T-1", lambda card: card["stages"].update({"one": {"status": "open"}}))
            with patch("friday.watch.notify") as notify:
                watch.local_poll(root, "T-1")
                watch.local_poll(root, "T-1")
                notify.assert_called_once_with("T-1", ["plan needs approval"])


if __name__ == "__main__":
    unittest.main()
