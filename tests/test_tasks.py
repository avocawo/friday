import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, doctor, milestone, resources, tasks, workspace
from friday.errors import FridayError
from test_milestone import MODELS, git


class TaskCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root = base / "home"
        origin, self.repo = base / "origin.git", base / "repo"
        git("init", "-q", "--bare", "-b", "main", str(origin))
        git("clone", "-q", str(origin), str(self.repo))
        (self.repo / "app.py").write_text("print('hi')\n")
        git("add", ".", cwd=self.repo)
        git("commit", "-qm", "init", cwd=self.repo)
        git("push", "-q", "origin", "HEAD:main", cwd=self.repo)
        self.profile_path = base / "profile.json"
        self.profile = {"name": "demo", "repository": str(self.repo), "base_branch": "main", "worktree_root": str(base / "wt"),
                        "models": MODELS, "roles": {"develop": ["worker"]}}
        self.profile_path.write_text(json.dumps(self.profile))
        card.create(self.root, "T-1", str(self.profile_path))
        self.worktree = workspace.create(self.root, "T-1", self.profile)

    def test_archive_removes_the_worktree_keeps_the_branch_and_frees_the_key(self):
        port = resources.port(self.root, "T-1")
        (Path(self.worktree) / "app.py").write_text("changed\n")
        with self.assertRaises(FridayError) as error:
            tasks.archive(self.root, "T-1", self.profile)
        self.assertEqual(error.exception.code, "dirty_tree")
        result = tasks.archive(self.root, "T-1", self.profile, force=True)
        self.assertFalse(Path(self.worktree).exists())
        self.assertTrue((Path(result["archive"]) / "card.json").is_file())
        self.assertIn("friday/T-1", git("branch", "--list", "friday/T-1", cwd=self.repo))
        self.assertNotIn("T-1", json.loads((self.root / "ports.json").read_text()))
        card.create(self.root, "T-1", str(self.profile_path))  # the key can start again
        self.assertEqual(resources.port(self.root, "T-1"), port)

    def test_stop_signals_the_running_process_and_refuses_otherwise(self):
        with self.assertRaises(FridayError):
            tasks.stop(self.root, "T-1")
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: sleeper.poll() is None and sleeper.kill())
        card.update(self.root, "T-1", lambda value: value.update(state="running", runner_pid=sleeper.pid))
        self.assertEqual(tasks.stop(self.root, "T-1"), sleeper.pid)
        self.assertEqual(sleeper.wait(5), -signal.SIGTERM)
        with self.assertRaises(FridayError):
            tasks.stop(self.root, "T-1")  # the process is gone

    def test_run_task_stops_cleanly_on_sigterm(self):
        card.update(self.root, "T-1", lambda value: value.update(state="approved", plan=dict(value["plan"], approved=True),
                                                                 milestones=[{"id": "M1", "state": "pending", "depends_on": []}]))
        (card.directory(self.root, "T-1") / "plan.json").write_text(json.dumps({"milestones": [{"id": "M1", "parallel": False}]}))
        def slow(root, key, profile, name, worktree=None):
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(5)
        with patch("friday.milestone.run", side_effect=slow), self.assertRaises(BaseException):
            milestone.run_task(self.root, "T-1", self.profile)
        value = card.read(self.root, "T-1")
        self.assertEqual(value["state"], "stopped")
        self.assertIs(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)

    def test_resume_restarts_failed_milestones(self):
        card.update(self.root, "T-1", lambda value: value.update(state="failed", plan=dict(value["plan"], approved=True),
                                                                 milestones=[{"id": "M1", "state": "failed", "depends_on": []}]))
        with patch("friday.milestone.run_task", return_value={"completed": [], "left": []}) as run:
            milestone.resume(self.root, "T-1", self.profile)
        run.assert_called_once()
        self.assertEqual(card.read(self.root, "T-1")["milestones"][0]["state"], "pending")
        card.update(self.root, "T-1", lambda value: value.update(state="awaiting_approval"))
        with self.assertRaises(FridayError):
            milestone.resume(self.root, "T-1", self.profile)

    def test_doctor_reports_missing_pieces_without_secrets(self):
        profile = dict(self.profile, roles={"develop": ["worker"]}, executor_commands=["npm test"])
        profile["models"] = dict(MODELS, glm={"model": "glm-5.3", "vendor": "zai", "driver": "claude", "tier": "strong",
                                              "token_file": "~/.missing-friday-token"})
        self.profile_path.write_text(json.dumps(profile))
        with patch.dict(os.environ, {"FRIDAY_HOME": str(self.root)}), patch("friday.doctor.shutil.which", return_value=None):
            report = doctor.inspect(self.profile_path)
        statuses = {item["name"]: item["status"] for item in report["checks"]}
        self.assertEqual((statuses["profile"], statuses["repository"], statuses["models"]), ("ok", "ok", "ok"))
        self.assertEqual(statuses["claude_cli"], "missing")
        self.assertEqual(statuses["token:glm"], "missing")
        self.assertEqual(statuses["gate"], "warning")
        self.assertFalse(report["ready"])
        text = doctor.render(report)
        self.assertIn("✗ claude_cli", text)
        self.assertIn("Не готово", text)
        broken = Path(self.directory.name) / "broken.json"
        broken.write_text("{")
        self.assertEqual(doctor.inspect(broken)["checks"][0]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
