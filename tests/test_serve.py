import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, serve, service, status

FRIDAY = Path(__file__).resolve().parents[1] / "bin" / "friday"
DEAD = 2 ** 22 + 12345  # a pid that is not running


class ServeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.spawned, self.told = [], []
        self.server = serve.Server(self.root, notify=lambda title, text: self.told.append((title, text)),
                                   spawn=lambda key, command: self.spawned.append((key, command)))

    def task(self, key, approved_at, state="approved", **fields):
        card.create(self.root, key, "profile.json")
        card.update(self.root, key, lambda value: value.update(state=state, plan=dict(value["plan"], approved=True), **fields))
        card.record(self.root, key, "plan_approved", by="owner")
        events = card.directory(self.root, key) / "events.jsonl"  # the approval time orders the queue
        lines = [json.loads(line) for line in events.read_text().splitlines()]
        lines[-1]["at"] = approved_at
        events.write_text("".join(json.dumps(line) + "\n" for line in lines))

    def test_queue_approvalTimeThenDecided_ordersTheQueue(self):
        self.task("T-1", "2026-11-19T10:00:00+00:00")
        self.task("T-2", "2026-11-19T09:00:00+00:00")
        self.task("T-3", "2026-11-19T11:00:00+00:00", state="stopped")
        for key in ("T-1", "T-2"):
            serve.enqueue(self.root, key)
        serve.enqueue(self.root, "T-3", first=True)  # continued after friday decide
        self.assertEqual(serve.queue(self.root), ["T-3", "T-2", "T-1"])

    def test_start_maxWorkersFromMachine_limitsRunningTasks(self):
        (self.root / "machine.json").write_text(json.dumps({"max_workers": 2}))
        self.task("T-0", "2026-11-19T08:00:00+00:00", state="running", runner_pid=os.getpid())
        for key, at in (("T-1", "2026-11-19T09:00:00+00:00"), ("T-2", "2026-11-19T10:00:00+00:00")):
            self.task(key, at)
            serve.enqueue(self.root, key)
        self.assertEqual(self.server.start(), ["T-1"])
        self.assertEqual(self.server.start(), [])  # T-1 counts as busy until its process marks the card running
        self.assertEqual(self.spawned, [("T-1", "run")])
        self.assertEqual(serve.queue(self.root), ["T-2"])
        self.assertEqual(status.card_state_name(card.read(self.root, "T-2")), "queued")

    def test_step_runningTaskWithDeadProcess_resumesIt(self):
        self.task("T-1", "2026-11-19T09:00:00+00:00", state="running", runner_pid=DEAD)
        self.assertEqual(self.server.step(), ["T-1"])
        self.assertEqual(self.spawned, [("T-1", "resume")])
        self.assertIn("serve_recovered", [event["type"] for event in card.events(self.root, "T-1")])

    def test_tell_taskLeavesRunning_notifiesQuestionStepFailureAndDone(self):
        endings = {"T-1": dict(state="stopped", questions=["Маску расширять?"]), "T-2": dict(state="stopped", owner_step="Соберите образ"),
                   "T-3": dict(state="failed"), "T-4": dict(state="done"), "T-5": dict(state="stopped")}
        for key in endings:
            self.task(key, "2026-11-19T09:00:00+00:00", state="running", runner_pid=os.getpid())
        self.task("T-6", "2026-11-19T09:00:00+00:00", state="done")  # finished before the server started: already known
        self.server.tell()
        for key, ending in endings.items():
            card.update(self.root, key, lambda value, ending=ending: value.update(**ending))
        self.server.tell()
        self.assertEqual(sorted(title for title, _ in self.told),
                         ["Friday: T-1 — question for the owner", "Friday: T-2 — owner step needed", "Friday: T-3 — failed",
                          "Friday: T-4 — done"])  # a plain stop by the owner says nothing
        self.assertIn(("Friday: T-1 — question for the owner", "Маску расширять?"), self.told)

    def test_reap_childShorterThanOnePoll_isStillReported(self):
        class Child:
            code = None
            def poll(self):
                return self.code
        child = Child()
        self.server.spawn = lambda key, command: child
        self.task("T-1", "2026-11-19T09:00:00+00:00", state="stopped")
        serve.enqueue(self.root, "T-1", "resume")
        self.server.step()
        card.update(self.root, "T-1", lambda value: value.update(state="stopped", owner_step="Соберите образ"))
        child.code = 0  # ran and stopped between two polls: no pass ever saw it running
        self.server.step()
        self.assertEqual(self.told, [("Friday: T-1 — owner step needed", "Соберите образ")])
        self.assertEqual(self.server.children, {})

    def test_step_unreadableCard_isSkippedAndLoggedOnce(self):
        logged = []
        self.server.log = logged.append
        self.task("T-1", "2026-11-19T09:00:00+00:00")
        serve.enqueue(self.root, "T-1")
        broken = card.directory(self.root, "T-0")
        broken.mkdir(parents=True)
        (broken / "card.json").write_text("{not json")
        self.assertEqual(self.server.step(), ["T-1"])
        self.server.step()
        self.assertEqual(len(logged), 1)

    def test_resumeCli_underServer_queuesForTheOwnerAndRunsInTheServersChild(self):
        from friday import cli
        self.task("T-1", "2026-11-19T09:00:00+00:00", state="stopped")
        (self.root / "serve.json").write_text(json.dumps({"pid": os.getpid()}))
        result = {"completed": [], "left": [], "delivery": None}
        with patch.dict(os.environ, {"FRIDAY_HOME": str(self.root)}), patch.object(cli, "task_profile", return_value={}), \
                patch.object(cli.milestone, "resume", return_value=result) as resume:
            os.environ.pop(serve.CHILD, None)
            self.assertEqual(cli.main(["resume", "T-1"]), 0)
            resume.assert_not_called()
            self.assertEqual(card.read(self.root, "T-1")["queued"]["command"], "resume")
            serve.dequeue(self.root, "T-1")
            with patch.dict(os.environ, {serve.CHILD: "1"}):  # the server's own child runs it instead of queueing again
                self.assertEqual(cli.main(["resume", "T-1"]), 0)
            resume.assert_called_once()
        self.assertNotIn("queued", card.read(self.root, "T-1"))

    def test_approveCli_withoutServer_launchesInTheBackgroundAsBefore(self):
        self.task("T-1", "2026-11-19T09:00:00+00:00")
        with patch("subprocess.Popen") as popen:
            from friday import cli
            text = cli.launch(self.root, "T-1")
        self.assertIn("friday serve is not running", text)
        self.assertEqual(popen.call_args.args[0][-2:], ["run", "T-1"])
        self.assertNotIn("queued", card.read(self.root, "T-1"))
        (self.root / "serve.json").write_text(json.dumps({"pid": os.getpid()}))  # a live server takes the task instead
        with patch("subprocess.Popen") as popen:
            text = cli.launch(self.root, "T-1", first=True)
        popen.assert_not_called()
        self.assertIn("queued in friday serve (position 1)", text)
        self.assertTrue(card.read(self.root, "T-1")["queued"]["first"])


class ServiceTests(unittest.TestCase):
    TOOLS = {"claude": "/opt/node/bin/claude", "codex": "/opt/codex/bin/codex", "docker": "/usr/local/bin/docker",
             "git": "/usr/bin/git", "glab": "/opt/homebrew/bin/glab"}

    def which(self, tool):
        return self.TOOLS.get(tool)

    def test_render_launchdAndSystemd_nameEveryToolPathAndTheHome(self):
        path, text = service.render("/srv/friday", "Darwin", self.which)
        agent = plistlib.loads(text.encode())
        self.assertEqual(path.name, "local.friday.serve.plist")
        self.assertEqual(agent["ProgramArguments"][-1], "serve")
        directories = agent["EnvironmentVariables"]["PATH"].split(":")
        for tool in self.TOOLS.values():
            self.assertIn(str(Path(tool).parent), directories)
        self.assertEqual(agent["EnvironmentVariables"]["FRIDAY_HOME"], str(Path("/srv/friday").resolve()))
        self.assertTrue(agent["KeepAlive"])
        _, unit = service.render("/srv/friday", "Linux", self.which)
        self.assertIn("ExecStart=", unit)
        self.assertIn('Environment="PATH=/opt/node/bin:', unit)

    def test_environment_missingRequiredTool_failsBeforeWriting(self):
        with self.assertRaises(Exception) as error:
            service.environment("/srv/friday", lambda tool: None if tool == "docker" else self.TOOLS.get(tool))
        self.assertIn("docker", str(error.exception))

    def test_check_runsDoctorInTheServiceEnvironmentOnly(self):
        calls = []
        def run(argv, env, **kwargs):
            calls.append((argv, env))
            return subprocess.CompletedProcess(argv, 0, "Готово к работе.")
        with patch.dict(os.environ, {"SECRET_FROM_SHELL": "x"}):
            code, output = service.check("/srv/friday", "profile.json", run=run, which=self.which)
        argv, env = calls[0]
        self.assertEqual((code, argv[-3:]), (0, ["doctor", "--profile", "profile.json"]))
        self.assertEqual(set(env), {"PATH", "FRIDAY_HOME", "HOME"})  # nothing leaks in from the calling shell


if __name__ == "__main__":
    unittest.main()
