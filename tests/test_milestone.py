import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, milestone, plan, workspace
from friday.errors import FridayError

# Each call takes the next entry of calls.json: files to write, commits to make, the structured answer and its session.
FAKE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
directory = os.environ["FAKE_DIR"]
prompt = sys.stdin.read()
counter = os.path.join(directory, "counter")
index = int(open(counter).read()) if os.path.exists(counter) else 0
open(counter, "w").write(str(index + 1))
with open(os.path.join(directory, "call-%d.json" % index), "w") as handle:
    json.dump({"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd()}, handle)
call = json.load(open(os.path.join(directory, "calls.json")))[index]
for path, content in call.get("write", {}).items():
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w").write(content)
for message in call.get("commit", []):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "add", "-A"], check=True, env=env)
    subprocess.run(["git", "commit", "-qm", message], check=True, env=env)
print(json.dumps({"type": "system", "subtype": "init", "session_id": call["session"]}), flush=True)
if call.get("quota"):
    print(json.dumps(call["quota"]), flush=True)
    print(json.dumps({"type": "result", "subtype": "error", "is_error": True, "session_id": call["session"], "result": "limit"}), flush=True)
    sys.exit(1)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": call["session"], "result": "",
                  "structured_output": call["answer"], "total_cost_usd": 0.01, "num_turns": 1,
                  "usage": {"input_tokens": 10, "output_tokens": 10}}), flush=True)
'''

MODELS = {
    "big": {"model": "big-1", "vendor": "alpha", "driver": "claude", "tier": "frontier"},
    "rival": {"model": "rival-1", "vendor": "beta", "driver": "claude", "tier": "frontier"},
    "worker": {"model": "worker-1", "vendor": "gamma", "driver": "claude", "tier": "strong"},
    "helper": {"model": "helper-1", "vendor": "gamma", "driver": "claude", "tier": "frontier"},
}
PLAN = {"summary": "Приветствие.", "rules": ["Тесты рядом (AGENTS.md)"], "assumptions": [], "questions": [], "responses": [],
        "milestones": [
            {"id": "M1", "title": "Функция", "goal": "greet", "complexity": "clear", "ui": False, "parallel": False,
             "depends_on": [], "files": ["greet.py"],
             "stages": [{"id": "M1.1", "brief": "greet", "steps": [], "files": [], "new_files": ["greet.py"], "estimate_lines": 5,
                         "criteria": [{"text": "greet есть", "check": "code"}]}]},
            {"id": "M2", "title": "Вывод", "goal": "print", "complexity": "clear", "ui": False, "parallel": False,
             "depends_on": ["M1"], "files": ["app.py"],
             "stages": [{"id": "M2.1", "brief": "print", "steps": [], "files": ["app.py"], "new_files": [], "estimate_lines": 5,
                         "criteria": [{"text": "app печатает", "check": "code"}]}]}]}
DONE = {"summary": "Сделано.", "stages_done": [], "extra_files": [], "disputes": []}
PASS = {"verdict": "pass", "criteria_results": [{"criterion": "c", "status": "pass", "evidence": "f:1"}], "findings": []}
BLOCK = {"verdict": "fail", "criteria_results": [], "findings": [
    {"severity": "blocking", "file": "greet.py", "problem": "Нет обработки пустого имени.", "evidence": "greet.py:1"},
    {"severity": "minor", "file": "greet.py", "problem": "Имя переменной.", "evidence": "greet.py:1"}]}


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")).stdout


class MilestoneTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root, self.fake = base / "home", base / "fake"
        (self.fake / "bin").mkdir(parents=True)
        (self.fake / "bin" / "claude").write_text(FAKE)
        (self.fake / "bin" / "claude").chmod(0o755)
        origin, repo = base / "origin.git", base / "repo"
        git("init", "-q", "--bare", "-b", "main", str(origin))
        git("clone", "-q", str(origin), str(repo))
        (repo / "app.py").write_text("print('hi')\n")
        git("add", ".", cwd=repo)
        git("commit", "-qm", "init", cwd=repo)
        git("push", "-q", "origin", "HEAD:main", cwd=repo)
        self.profile_path = base / "profile.json"
        self.profile = {"name": "demo", "repository": str(repo), "base_branch": "main", "worktree_root": str(base / "wt"),
                        "models": MODELS, "executor_commands": ["python3 -m pytest"],
                        "roles": {"plan": ["big"], "plan-review": ["rival"], "develop": ["worker", "helper"], "review": ["big"]}}
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
        self.calls(("p", None, PLAN), ("r", None, {"verdict": "ok", "findings": []}))
        plan.make(self.root, "T-1", self.profile, "Приветствие")
        plan.approve(self.root, "T-1")

    def calls(self, *entries):
        (self.fake / "calls.json").write_text(json.dumps([dict(session=s, answer=a, **(extra or {})) for s, extra, a in entries]))
        (self.fake / "counter").unlink(missing_ok=True)
        for old in self.fake.glob("call-*.json"):
            old.unlink()

    def call(self, index):
        return json.loads((self.fake / ("call-%d.json" % index)).read_text())

    def test_milestones_run_in_order_with_review_fix_and_dispute(self):
        self.calls(("e1", {"write": {"greet.py": "def greet(n):\n    return n\n"}, "commit": ["T-1 M1.1: greet"]}, DONE),
                   ("rev", None, BLOCK),
                   ("e1", {"write": {"greet.py": "def greet(n):\n    return n or '?'\n"}, "commit": ["T-1 M1.1: empty name"]}, DONE),
                   ("rev", None, dict(BLOCK, findings=[{"severity": "blocking", "file": "greet.py", "problem": "Нужен docstring.",
                                                       "evidence": "greet.py:1"}])),
                   ("e1", None, dict(DONE, disputes=[{"finding": 0, "reason": "В проекте docstring не пишут.", "evidence": "AGENTS.md:3"}])),
                   ("e2", {"write": {"app.py": "from greet import greet\nprint(greet('A'))\n"}, "commit": ["T-1 M2.1: print"]}, DONE),
                   ("rev", None, PASS))
        result = milestone.run_task(self.root, "T-1", self.profile)
        self.assertEqual(([item["milestone"] for item in result["completed"]], result["left"]), (["M1", "M2"], []))
        value = card.read(self.root, "T-1")
        self.assertEqual(value["state"], "done")
        first = value["milestones"][0]
        self.assertEqual((first["state"], first["stages"][0]["commit"] is not None), ("done", True))
        self.assertEqual(first["minor_findings"], [])
        self.assertEqual([(d["problem"], d["executor_reason"]) for d in value["disputes"]], [("Нужен docstring.", "В проекте docstring не пишут.")])
        executor, reviewer, fix = self.call(0), self.call(1), self.call(2)
        self.assertEqual(executor["argv"][executor["argv"].index("--model") + 1], "worker-1")
        settings = json.loads(executor["argv"][executor["argv"].index("--settings") + 1])
        self.assertTrue(settings["sandbox"]["enabled"])
        self.assertIn("Bash(python3 -m pytest:*)", settings["permissions"]["allow"])
        self.assertIn("Bash(git commit:*)", settings["permissions"]["allow"])
        self.assertIn("- python3 -m pytest\n", executor["prompt"])
        self.assertEqual(reviewer["argv"][reviewer["argv"].index("--model") + 1], "big-1")
        self.assertNotIn("--permission-mode", reviewer["argv"])
        self.assertIn("+def greet(n):", reviewer["prompt"])
        self.assertEqual(fix["argv"][fix["argv"].index("--resume") + 1], "e1")
        self.assertIn("0. [blocking] greet.py: Нет обработки пустого имени.", fix["prompt"])
        self.assertNotIn("Имя переменной", fix["prompt"])
        self.assertIn("Нет обработки пустого имени.", self.call(3)["prompt"])  # the reviewer sees what it asked to fix
        m2_review = self.call(6)["prompt"]
        self.assertIn("+print(greet('A'))", m2_review)
        self.assertNotIn("+def greet", m2_review)  # milestone diff only
        types = [event["type"] for event in card.events(self.root, "T-1")]
        self.assertEqual(types.count("milestone_done"), 2)

    def test_gate_failures_feed_errors_and_escalate_after_two(self):
        self.profile.update(gate=["check"], gate_image="img")
        outcomes = iter([{"status": "failed", "log": "fail-1"}, {"status": "failed", "log": "fail-2"}, {"status": "passed", "log": "ok"}])
        def fake_gate(root, profile, worktree, base, log, on_wait=None):
            record = dict(next(outcomes), head="h", exit_code=0, unchanged=True)
            Path(log).parent.mkdir(parents=True, exist_ok=True)
            Path(log).write_text("—→ ESLint\n  3:7 error no-shadow\n—→ Tests\n ✓ ok\n" if record["status"] == "failed" else "fine")
            record["log"] = str(log)
            return record
        self.calls(("e1", {"write": {"greet.py": "x = 1\n"}, "commit": ["T-1 M1.1: greet"]}, DONE),
                   ("e1", {"write": {"greet.py": "x = 2\n"}, "commit": ["T-1 M1.1: fix"]}, DONE),
                   ("e2", {"write": {"greet.py": "x = 3\n"}, "commit": ["T-1 M1.1: fix again"]}, DONE),
                   ("rev", None, PASS))
        with patch("friday.milestone.gate.check", side_effect=fake_gate):
            milestone.run(self.root, "T-1", self.profile, "M1")
        second, third = self.call(1), self.call(2)
        self.assertIn("error no-shadow", second["prompt"])
        self.assertNotIn("✓ ok", second["prompt"])
        self.assertEqual(third["argv"][third["argv"].index("--model") + 1], "helper-1")
        self.assertNotIn("--resume", third["argv"])
        self.assertIn("Continue from them", third["prompt"])
        notes = [event.get("note", "") for event in card.events(self.root, "T-1") if event["type"] == "cast_note"]
        self.assertTrue(any("два провала подряд" in note for note in notes))
        self.assertEqual(card.read(self.root, "T-1")["milestones"][0]["state"], "done")

    def test_gate_that_never_passes_fails_the_milestone(self):
        self.profile.update(gate=["check"], gate_image="img", milestone_rounds=2)
        def failing(root, profile, worktree, base, log, on_wait=None):
            Path(log).parent.mkdir(parents=True, exist_ok=True)
            Path(log).write_text("error: broken")
            return {"status": "failed", "log": str(log), "head": "h", "exit_code": 1, "unchanged": True}
        self.calls(("e1", {"write": {"greet.py": "a\n"}, "commit": ["T-1 M1.1: a"]}, DONE),
                   ("e1", {"write": {"greet.py": "b\n"}, "commit": ["T-1 M1.1: b"]}, DONE))
        with patch("friday.milestone.gate.check", side_effect=failing), self.assertRaises(FridayError) as error:
            milestone.run_task(self.root, "T-1", self.profile)
        self.assertEqual(error.exception.code, "milestone_failed")
        self.assertEqual(card.read(self.root, "T-1")["state"], "failed")

    def test_unresolved_review_findings_go_to_the_owner_without_stopping(self):
        self.profile["milestone_rounds"] = 2
        self.calls(("e1", {"write": {"greet.py": "a\n"}, "commit": ["T-1 M1.1: a"]}, DONE), ("rev", None, BLOCK),
                   ("e1", {"write": {"greet.py": "b\n"}, "commit": ["T-1 M1.1: b"]}, DONE), ("rev", None, BLOCK))
        milestone.run(self.root, "T-1", self.profile, "M1")
        value = card.read(self.root, "T-1")
        self.assertEqual(value["milestones"][0]["state"], "done")
        self.assertEqual([d["problem"] for d in value["disputes"]], ["Нет обработки пустого имени."])

    def test_leftover_changes_are_committed_and_outside_files_noted(self):
        self.calls(("e1", {"write": {"greet.py": "a\n", "notes.txt": "x\n"}}, DONE), ("rev", None, PASS))
        result = milestone.run(self.root, "T-1", self.profile, "M1")
        self.assertEqual(result["outside_files"], ["notes.txt"])
        self.assertEqual(git("status", "--porcelain", cwd=self.worktree), "")
        self.assertIn("left uncommitted", git("log", "-1", "--format=%s", cwd=self.worktree))

    def test_decision_reopens_only_named_milestones_and_their_dependents(self):
        def finish(value):
            for item in value["milestones"]:
                item["state"] = "done"
        card.update(self.root, "T-1", finish)
        card.update(self.root, "T-1", lambda value: value.update(state="done"))
        self.assertEqual(milestone.decide(self.root, "T-1", "names", "Пустое имя — ошибка."), [])
        self.assertEqual(milestone.decide(self.root, "T-1", "names", "Пустое имя — ошибка.", ["M1"]), ["M1", "M2"])
        value = card.read(self.root, "T-1")
        self.assertEqual([item["state"] for item in value["milestones"]], ["reopened", "reopened"])
        self.assertEqual(value["state"], "stopped")
        self.assertEqual(len(value["decisions"]), 2)
        self.calls(("e1", {"write": {"greet.py": "z\n"}, "commit": ["T-1 M1.1: decision"]}, DONE), ("rev", None, PASS))
        milestone.run(self.root, "T-1", self.profile, "M1")
        prompt = self.call(0)["prompt"]
        self.assertIn("Пустое имя — ошибка.", prompt)
        self.assertIn("Update the existing implementation", prompt)
        with self.assertRaises(FridayError):
            milestone.decide(self.root, "T-1", "x", "y", ["M9"])

    def test_executor_that_never_commits_fails_instead_of_closing(self):
        self.profile["milestone_rounds"] = 2
        self.calls(("e1", None, DONE), ("e1", None, DONE))
        with self.assertRaises(FridayError) as error:
            milestone.run(self.root, "T-1", self.profile, "M1")
        self.assertEqual(error.exception.code, "milestone_failed")
        self.assertEqual(card.read(self.root, "T-1")["milestones"][0]["state"], "failed")
        self.assertEqual(card.events(self.root, "T-1")[-1]["reason"], "no commits")

    def test_reviewer_out_of_quota_is_replaced(self):
        limit = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 4102444800}}
        calls = [{"session": "e1", "answer": DONE, "write": {"greet.py": "a\n"}, "commit": ["T-1 M1.1: a"]},
                 {"session": "rev", "answer": None, "quota": limit}, {"session": "rev2", "answer": PASS}]
        (self.fake / "calls.json").write_text(json.dumps(calls))
        (self.fake / "counter").unlink(missing_ok=True)
        milestone.run(self.root, "T-1", self.profile, "M1")
        first, second = self.call(1), self.call(2)
        self.assertEqual(first["argv"][first["argv"].index("--model") + 1], "big-1")
        self.assertEqual(second["argv"][second["argv"].index("--model") + 1], "rival-1")
        self.assertEqual(card.read(self.root, "T-1")["milestones"][0]["state"], "done")

    def test_executor_gets_the_owner_git_identity(self):
        git("config", "user.name", "Owner", cwd=self.worktree)
        git("config", "user.email", "owner@example.com", cwd=self.worktree)
        self.calls(("e1", {"write": {"greet.py": "a\n"}, "commit": ["T-1 M1.1: a"]}, DONE), ("rev", None, PASS))
        with patch("friday.runner.run", wraps=__import__("friday").runner.run) as spy:
            milestone.run(self.root, "T-1", self.profile, "M1")
        executor_spec = spy.call_args_list[0].args[0]
        self.assertEqual((executor_spec.env["GIT_AUTHOR_NAME"], executor_spec.env["GIT_COMMITTER_EMAIL"]), ("Owner", "owner@example.com"))
        self.assertNotIn("GIT_AUTHOR_NAME", spy.call_args_list[1].args[0].env)

    def test_running_needs_an_approved_plan(self):
        card.update(self.root, "T-1", lambda value: value.update(state="awaiting_approval", plan=dict(value["plan"], approved=False)))
        with self.assertRaises(FridayError):
            milestone.run_task(self.root, "T-1", self.profile)


if __name__ == "__main__":
    unittest.main()
