import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, milestone, resources, runner, status, workspace
from test_milestone import DONE, MODELS, PASS, git
from test_runner import FAKE as STREAM_FAKE, INIT, alive

# Parallel lanes call the fake concurrently, so answers are chosen by milestone and role, not by call order.
FAKE = r'''#!/usr/bin/env python3
import json, os, re, subprocess, sys, time

os.environ["FRIDAY_LANG"] = "en"  # assertions read Friday's English messages
directory = os.environ["FAKE_DIR"]
prompt = sys.stdin.read()
milestone = re.search(r'"id": "(M\d)"', prompt).group(1)
kind = "review" if prompt.startswith("You review") else "develop"
counter = os.path.join(directory, "%s-%s" % (milestone, kind))
index = int(open(counter).read()) if os.path.exists(counter) else 0
open(counter, "w").write(str(index + 1))
call = json.load(open(os.path.join(directory, "calls.json")))[milestone][kind][index]
with open(os.path.join(directory, "log.jsonl"), "a") as handle:
    handle.write(json.dumps({"milestone": milestone, "kind": kind, "start": time.time(), "cwd": os.getcwd(), "prompt": prompt}) + "\n")
time.sleep(call.get("sleep", 0))
for path, content in call.get("write", {}).items():
    open(path, "w").write(content)
for message in call.get("commit", []):
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", message], check=True)
print(json.dumps({"type": "system", "subtype": "init", "session_id": milestone + kind}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": milestone + kind, "result": "",
                  "structured_output": call["answer"], "total_cost_usd": 0.01, "num_turns": 1, "usage": {}}), flush=True)
'''


def stage(name, files, new):
    return {"id": name + ".1", "brief": name, "steps": [], "files": files, "new_files": new, "estimate_lines": 5,
            "criteria": [{"text": name + " готово", "check": "code"}]}


PLAN = {"summary": "Параллельно.", "rules": [], "assumptions": [], "questions": [], "responses": [], "milestones": [
    {"id": "M1", "title": "Один", "goal": "a", "complexity": "clear", "ui": False, "parallel": True, "depends_on": [],
     "files": ["a.py"], "stages": [stage("M1", [], ["a.py"])]},
    {"id": "M2", "title": "Два", "goal": "b", "complexity": "clear", "ui": False, "parallel": True, "depends_on": [],
     "files": ["b.py"], "stages": [stage("M2", [], ["b.py"])]},
    {"id": "M3", "title": "Три", "goal": "c", "complexity": "clear", "ui": False, "parallel": False, "depends_on": ["M1", "M2"],
     "files": ["app.py"], "stages": [stage("M3", ["app.py"], [])]}]}


class SlotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_slots_follow_machine_capacity_and_report_waiting(self):
        waits = []
        with resources.slot(self.root, "gate"):
            released = threading.Event()
            def second():
                with resources.slot(self.root, "gate", on_wait=waits.append):
                    released.set()
            worker = threading.Thread(target=second)
            worker.start()
            time.sleep(0.5)
            self.assertFalse(released.is_set())
        worker.join(5)
        self.assertEqual(waits, ["gate"])
        (self.root / "machine.json").write_text(json.dumps({"slots": {"gate": 2}}))
        with resources.slot(self.root, "gate"), resources.slot(self.root, "gate", timeout=1) as index:
            self.assertEqual(index, 1)
        with resources.slot(self.root, "vendor:anything") as index:
            self.assertIsNone(index)  # vendors are unlimited unless machine.json limits them

    def test_preview_ports_are_stable_unique_and_released(self):
        first, second = resources.port(self.root, "T-1"), resources.port(self.root, "T-2")
        self.assertNotEqual(first, second)
        self.assertEqual(resources.port(self.root, "T-1"), first)
        resources.release_port(self.root, "T-1")
        self.assertEqual(resources.port(self.root, "T-3"), first)

    def test_cancel_stops_a_running_agent_and_its_children(self):
        fake = self.root / "fake"
        (fake / "bin").mkdir(parents=True)
        (fake / "bin" / "claude").write_text(STREAM_FAKE)
        (fake / "bin" / "claude").chmod(0o755)
        (fake / "scenario.json").write_text(json.dumps([{"emit": INIT}, {"child": True}, {"sleep": 300}]))
        spec = runner.Spec(driver="claude", model="m", prompt="p", cwd=str(self.root), log_dir=str(self.root / "logs"),
                           env={"FAKE_DIR": str(fake)})
        threading.Timer(3.0, runner.CANCEL.set).start()  # after the fake agent has started its child on a loaded machine
        self.addCleanup(runner.CANCEL.clear)
        with patch.dict(os.environ, {"PATH": str(fake / "bin") + os.pathsep + os.environ.get("PATH", "")}):
            result = runner.run(spec)
        self.assertEqual(result.status, "cancelled")
        child = int((fake / "child.pid").read_text())
        deadline = time.time() + 5
        while alive(child) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(child))


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root, self.fake = base / "home", base / "fake"
        (self.fake / "bin").mkdir(parents=True)
        origin, repo = base / "origin.git", base / "repo"
        git("init", "-q", "--bare", "-b", "main", str(origin))
        git("clone", "-q", str(origin), str(repo))
        (repo / "app.py").write_text("print('hi')\n")
        git("add", ".", cwd=repo)
        git("commit", "-qm", "init", cwd=repo)
        git("push", "-q", "origin", "HEAD:main", cwd=repo)
        git("config", "user.name", "Owner", cwd=repo)
        git("config", "user.email", "owner@example.com", cwd=repo)
        self.profile_path = base / "profile.json"
        self.profile = {"name": "demo", "repository": str(repo), "base_branch": "main", "worktree_root": str(base / "wt"),
                        "models": MODELS, "roles": {"plan": ["big"], "plan-review": ["rival"], "develop": ["worker"], "review": ["big"]}}
        self.profile_path.write_text(json.dumps(self.profile))
        self.profile["path"] = str(self.profile_path)
        for patcher in (patch.dict(os.environ, {"PATH": str(self.fake / "bin") + os.pathsep + os.environ.get("PATH", ""),
                                                "FAKE_DIR": str(self.fake)}),
                        patch("friday.models.environment", return_value={"FAKE_DIR": str(self.fake)}),
                        patch("friday.milestone.finish", return_value=None)):  # the final phase has its own tests
            patcher.start()
            self.addCleanup(patcher.stop)
        card.create(self.root, "T-1", str(self.profile_path))
        self.worktree = workspace.create(self.root, "T-1", self.profile)
        document = dict(PLAN)
        (card.directory(self.root, "T-1") / "plan.json").write_text(json.dumps(document))
        skeleton = [{"id": m["id"], "title": m["title"], "complexity": "clear", "ui": False, "parallel": m["parallel"],
                     "depends_on": m["depends_on"], "state": "pending", "stages": []} for m in document["milestones"]]
        layout = {role: dict(MODELS[name], name=name) for role, name in
                  {"plan": "big", "plan-review": "rival", "develop": "worker", "review": "big", "final-review": "big",
                   "blind-code": "rival", "browser": "big"}.items()}
        card.update(self.root, "T-1", lambda value: value.update(state="approved", milestones=skeleton, cast=layout,
                                                                 plan=dict(value["plan"], approved=True)))
        (self.fake / "bin" / "claude").write_text(FAKE)
        (self.fake / "bin" / "claude").chmod(0o755)

    def answers(self, **milestones):
        (self.fake / "calls.json").write_text(json.dumps(milestones))

    def log(self):
        return [json.loads(line) for line in (self.fake / "log.jsonl").read_text().splitlines()]

    def test_parallel_milestones_overlap_in_lanes_and_land_on_the_task_branch(self):
        self.answers(M1={"develop": [{"answer": DONE, "sleep": 1, "write": {"a.py": "a = 1\n"}, "commit": ["T-1 M1.1: a"]}],
                         "review": [{"answer": PASS}]},
                     M2={"develop": [{"answer": DONE, "sleep": 1, "write": {"b.py": "b = 1\n"}, "commit": ["T-1 M2.1: b"]}],
                         "review": [{"answer": PASS}]},
                     M3={"develop": [{"answer": DONE, "write": {"app.py": "import a, b\n"}, "commit": ["T-1 M3.1: app"]}],
                         "review": [{"answer": PASS}]})
        result = milestone.run_task(self.root, "T-1", self.profile)
        self.assertEqual(sorted(item["milestone"] for item in result["completed"]), ["M1", "M2", "M3"])
        log = self.log()
        lanes = {entry["milestone"]: entry for entry in log if entry["kind"] == "develop"}
        self.assertLess(abs(lanes["M1"]["start"] - lanes["M2"]["start"]), 0.9)  # both executors ran at the same time
        self.assertNotEqual(lanes["M1"]["cwd"], lanes["M2"]["cwd"])
        self.assertEqual(os.path.realpath(lanes["M3"]["cwd"]), os.path.realpath(self.worktree))
        subjects = git("log", "--format=%s", cwd=self.worktree).splitlines()
        self.assertEqual(subjects[:4], ["T-1 M3.1: app", "T-1 M2.1: b", "T-1 M1.1: a", "init"])
        value = card.read(self.root, "T-1")
        shas = set(git("log", "--format=%H", cwd=self.worktree).split())
        for item in value["milestones"]:
            self.assertEqual(item["state"], "done")
            self.assertIn(item["stages"][0]["commit"], shas)
        self.assertEqual(git("worktree", "list", cwd=self.worktree).count("\n"), 2)  # main repo and task worktree only
        self.assertEqual(git("log", "-1", "--format=%an <%ae>", cwd=self.worktree), "Owner <owner@example.com>\n")
        m3_review = next(entry for entry in log if entry["milestone"] == "M3" and entry["kind"] == "review")["prompt"]
        self.assertIn("+import a, b", m3_review)
        self.assertNotIn("+a = 1", m3_review)

    def test_vendor_slot_serializes_lanes_and_status_shows_the_wait(self):
        (self.root / "machine.json").write_text(json.dumps({"slots": {"vendor:gamma": 1}}))
        seen = []
        original = card.waiting
        def spy(root, key, resource, milestone=None):
            original(root, key, resource, milestone)
            seen.append(status.render(status.overview(root)))
        self.answers(M1={"develop": [{"answer": DONE, "sleep": 1.5, "write": {"a.py": "a\n"}, "commit": ["T-1 M1.1: a"]}],
                         "review": [{"answer": PASS}]},
                     M2={"develop": [{"answer": DONE, "sleep": 1.5, "write": {"b.py": "b\n"}, "commit": ["T-1 M2.1: b"]}],
                         "review": [{"answer": PASS}]},
                     M3={"develop": [{"answer": DONE, "write": {"app.py": "x\n"}, "commit": ["T-1 M3.1: app"]}],
                         "review": [{"answer": PASS}]})
        with patch("friday.card.waiting", side_effect=spy):
            milestone.run_task(self.root, "T-1", self.profile)
        starts = sorted(entry["start"] for entry in self.log() if entry["kind"] == "develop" and entry["milestone"] in ("M1", "M2"))
        self.assertGreaterEqual(starts[1] - starts[0], 1.4)
        self.assertTrue(any("waiting for a slot: vendor:gamma" in text for text in seen))
        self.assertFalse(any("LOST" in text for text in seen))

    def test_failed_lane_still_lands_the_good_one_and_cleans_up(self):
        never = {"answer": DONE}
        self.profile["milestone_rounds"] = 1
        self.answers(M1={"develop": [{"answer": DONE, "write": {"a.py": "a\n"}, "commit": ["T-1 M1.1: a"]}], "review": [{"answer": PASS}]},
                     M2={"develop": [never], "review": []})
        with self.assertRaises(milestone.FridayError):
            milestone.run_task(self.root, "T-1", self.profile)
        states = {item["id"]: item["state"] for item in card.read(self.root, "T-1")["milestones"]}
        self.assertEqual((states["M1"], states["M2"]), ("done", "failed"))
        self.assertIn("T-1 M1.1: a", git("log", "--format=%s", cwd=self.worktree))
        self.assertEqual(git("worktree", "list", cwd=self.worktree).count("\n"), 2)


if __name__ == "__main__":
    unittest.main()
