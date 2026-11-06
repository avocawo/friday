import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, runner, status
from friday.errors import FridayError
from test_runner import ANSWER, FAKE, INIT, RESULT, alive

os.environ["FRIDAY_LANG"] = "en"  # assertions read Friday's English messages

CLI = Path(__file__).resolve().parents[1] / "bin" / "friday"


class CardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "home"
        self.fake = Path(self.directory.name) / "fake"
        (self.fake / "bin").mkdir(parents=True)
        for name in ("claude", "codex"):
            (self.fake / "bin" / name).write_text(FAKE)
            (self.fake / "bin" / name).chmod(0o755)
        path = patch.dict(os.environ, {"PATH": str(self.fake / "bin") + os.pathsep + os.environ.get("PATH", "")})
        path.start()
        self.addCleanup(path.stop)
        card.create(self.root, "T-1", "/profiles/demo.json")

    def scenario(self, steps):
        (self.fake / "scenario.json").write_text(json.dumps(steps))

    def spec(self, **changes):
        values = dict(driver="claude", model="model-a", prompt="do it", cwd=self.directory.name, log_dir="",
                      env={"FAKE_DIR": str(self.fake)})
        values.update(changes)
        return runner.Spec(**values)

    def test_cards_are_created_once_and_validated(self):
        value = card.read(self.root, "T-1")
        self.assertEqual((value["version"], value["state"], value["current"]), (2, "planning", {}))
        with self.assertRaises(FridayError):
            card.create(self.root, "T-1", "/other.json")
        with self.assertRaises(FridayError):
            card.create(self.root, "../escape", "/other.json")
        with self.assertRaises(FridayError):
            card.update(self.root, "T-1", lambda value: value.update(state="unknown"))
        self.assertEqual(card.read(self.root, "T-1")["state"], "planning")
        self.assertEqual([event["type"] for event in card.events(self.root, "T-1")], ["created"])

    def test_running_agent_is_visible_in_the_card_and_status(self):
        self.scenario([{"emit": INIT}, {"emit": ANSWER}, {"sleep": 1.1}, {"emit": RESULT}])
        seen = []
        def observe(event):
            if event["note"].startswith("text"):
                time.sleep(1.05)  # let the throttled status file catch up
                seen.append(status.overview(self.root)[0])
        result = card.run_agent(self.root, "T-1", self.spec(), role="develop", milestone="M1",
                                entry={"name": "glm", "vendor": "zai", "tier": "strong"},
                                on_event=observe)
        self.assertEqual(result.status, "ok")
        now = seen[0]["now"][0]
        self.assertEqual((now["role"], now["milestone"], now["model"], now["lost"]), ("develop", "M1", "model-a", False))
        self.assertIsNotNone(now["last_event"])
        self.assertEqual(card.read(self.root, "T-1")["current"], {})
        finished = card.events(self.root, "T-1")[-1]
        self.assertEqual((finished["type"], finished["status"], finished["usage"]["cost_usd"], finished["vendor"]),
                         ("run_finished", "ok", 0.25, "zai"))
        self.assertTrue((card.directory(self.root, "T-1") / "runs" / finished["run"] / "transcript.jsonl").is_file())

    def test_a_second_run_waits_for_the_live_one(self):
        self.scenario([{"emit": INIT}, {"emit": ANSWER}, {"emit": RESULT}])
        def nested(event):
            if event["note"] == "started":
                with self.assertRaises(FridayError) as error:
                    card.run_agent(self.root, "T-1", self.spec(), role="review")
                self.assertEqual(error.exception.code, "task_busy")
        self.assertEqual(card.run_agent(self.root, "T-1", self.spec(), role="develop", on_event=nested).status, "ok")

    def test_lost_run_is_reported_and_its_orphan_killed(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        orphan = subprocess.Popen(["sleep", "300"], start_new_session=True)
        self.addCleanup(lambda: orphan.poll() is None and orphan.kill())
        logs = card.directory(self.root, "T-1") / "runs" / "r1"
        logs.mkdir(parents=True)
        (logs / "status.json").write_text(json.dumps({"state": "running", "pid": orphan.pid, "owner": dead.pid,
                                                      "last_activity_at": time.time(), "usage": {}}))
        current = {"run": "r1", "role": "develop", "milestone": None, "phase": "develop", "model": "m", "driver": "claude",
                   "started_at": time.time()}
        card.update(self.root, "T-1", lambda value: value.update(state="running", current=current))
        self.assertTrue(status.overview(self.root)[0]["now"][0]["lost"])
        self.assertIn("LOST", status.render(status.overview(self.root)))
        self.assertEqual(card.recover(self.root, "T-1")[0]["run"], "r1")
        orphan.wait(5)
        self.assertFalse(alive(orphan.pid))
        self.assertEqual(card.read(self.root, "T-1")["current"], {})
        self.assertEqual(card.events(self.root, "T-1")[-1]["status"], "lost")

    def test_spending_is_summed_by_role_with_unpriced_runs_counted(self):
        self.scenario([{"emit": INIT}, {"emit": RESULT}])
        card.run_agent(self.root, "T-1", self.spec(), role="develop")
        card.run_agent(self.root, "T-1", self.spec(), role="review")
        self.scenario([{"emit": {"type": "thread.started", "thread_id": "t"}},
                       {"emit": {"type": "turn.completed", "usage": {"input_tokens": 1000, "output_tokens": 500}}}])
        card.run_agent(self.root, "T-1", self.spec(driver="codex"), role="review")
        spent = status.overview(self.root)[0]["spent"]
        self.assertEqual((spent["runs"], spent["unpriced_runs"], round(spent["cost_usd"], 2)), (3, 1, 0.5))
        self.assertEqual(spent["by_role"]["review"]["runs"], 2)
        self.assertEqual(spent["tokens"], 2 * (4 + 9 + 70 + 110) + 1500)
        text = status.render(status.overview(self.root))
        self.assertIn("$0.50 +1 unpriced", text)
        self.assertIn("review: 2 runs", status.render_task(self.root, "T-1"))

    def test_status_command_lists_tasks(self):
        card.create(self.root, "T-2", "/profiles/demo.json")
        environment = dict(os.environ, FRIDAY_HOME=str(self.root))
        result = subprocess.run([sys.executable, str(CLI), "status", "--json"], capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["key"] for row in json.loads(result.stdout)], ["T-1", "T-2"])
        result = subprocess.run([sys.executable, str(CLI), "status"], capture_output=True, text=True, env=environment)
        self.assertIn("T-2", result.stdout)
        self.assertIn("TASK", result.stdout)
        result = subprocess.run([sys.executable, str(CLI), "status", "MISSING"], capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
