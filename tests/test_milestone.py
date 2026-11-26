import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, milestone, models, plan, workspace
from friday.errors import FridayError

REAL_FINISH = milestone.finish  # setUp replaces it; the dist test needs the real one

# Each call takes the next entry of calls.json: files to write, commits to make, the structured answer and its session.
FAKE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys

os.environ["FRIDAY_LANG"] = "en"  # assertions read Friday's English messages
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
for step in call.get("steps", []):  # several commits in one session, each with its own files
    for path, content in step["write"].items():
        open(path, "w").write(content)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "add", "-A"], check=True, env=env)
    subprocess.run(["git", "commit", "-qm", step["message"]], check=True, env=env)
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
DONE = {"summary": "Сделано.", "stages_done": [], "extra_files": [], "disputes": [], "checks": "passed", "checks_note": ""}
PASS = {"verdict": "pass", "criteria_results": [{"criterion": "c", "status": "pass", "evidence": "f:1"}], "findings": []}
STATEMENT = "Pinned public payload: sha256=ee845b8abcc5107441c452a5454a5711b1dabec34b64bc5f370688003d7bf2ff; https://example.test/base.qcow2"
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
        plan.make(self.root, "T-1", self.profile, STATEMENT)
        plan.approve(self.root, "T-1")

    def calls(self, *entries):
        (self.fake / "calls.json").write_text(json.dumps([dict(session=s, answer=a, **(extra or {})) for s, extra, a in entries]))
        (self.fake / "counter").unlink(missing_ok=True)
        for old in self.fake.glob("call-*.json"):
            old.unlink()

    def call(self, index):
        return json.loads((self.fake / ("call-%d.json" % index)).read_text())

    def test_exact_task_statement_reaches_executor_and_reviewer(self):
        self.assertEqual(card.read(self.root, "T-1").get("task_statement"), STATEMENT)
        self.calls(("e1", {"write": {"greet.py": "def greet(n):\n    return n\n"}, "commit": ["T-1 M1.1: greet"]}, DONE),
                   ("rev", None, PASS),
                   ("e2", {"write": {"app.py": "print(1)\n"}, "commit": ["T-1 M2.1: print"]}, DONE),
                   ("rev", None, PASS))
        milestone.run_task(self.root, "T-1", self.profile)
        for index in range(4):
            self.assertIn(STATEMENT, self.call(index)["prompt"])

    def test_legacy_statement_restore_requires_original_planning_identity(self):
        card.update(self.root, "T-1", lambda value: value.pop("task_statement", None))
        before = card.read(self.root, "T-1")
        with self.assertRaises(FridayError):
            plan.restore_statement(self.root, "T-1", self.profile, STATEMENT + " changed")
        self.assertEqual(card.read(self.root, "T-1"), before)
        plan.restore_statement(self.root, "T-1", self.profile, STATEMENT)
        after = card.read(self.root, "T-1")
        self.assertEqual(after["task_statement"], STATEMENT)
        for name in ("state", "plan", "milestones", "budget_usage"):
            self.assertEqual(after.get(name), before.get(name))
        with self.assertRaises(FridayError):
            plan.restore_statement(self.root, "T-1", self.profile, STATEMENT + " changed")

    def test_resume_cli_restores_only_the_original_brief(self):
        card.update(self.root, "T-1", lambda value: value.pop("task_statement", None))
        brief = Path(self.directory.name) / "task.md"
        cli = Path(__file__).resolve().parents[1] / "bin" / "friday"
        script = ("import runpy,sys; from friday import milestone; "
                  "milestone.resume=lambda *a:dict(completed=[],left=['M1']); "
                  "sys.argv=['friday','resume','T-1','--brief',sys.argv[1]]; "
                  "runpy.run_path(%r,run_name='__main__')") % str(cli)
        environment = dict(os.environ, FRIDAY_HOME=str(self.root))
        before = card.read(self.root, "T-1")
        for text, code in ((STATEMENT + " changed", 1), (STATEMENT, 0)):
            brief.write_text(text)
            result = subprocess.run([sys.executable, "-c", script, str(brief)], env=environment,
                                    capture_output=True, text=True, cwd=cli.parents[1])
            self.assertEqual(result.returncode, code, result.stderr)
            if code:
                self.assertIn("does not match", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(card.read(self.root, "T-1"), before)
            else:
                self.assertIn("Milestones done: 0", result.stdout)
                self.assertEqual(result.stderr, "")
                self.assertEqual(card.read(self.root, "T-1")["task_statement"], STATEMENT)

    def test_resume_reviews_retained_commits_without_requiring_a_dummy_commit(self):
        before = card.read(self.root, "T-1")["base"]
        (Path(self.worktree) / 'greet.py').write_text('def greet(n):\n    return n\n')
        git('add', 'greet.py', cwd=self.worktree)
        git('commit', '-qm', 'T-1 M1.1: retained implementation', cwd=self.worktree)
        def failed(value):
            value['state'] = 'failed'
            value['milestones'][0].update(state='failed', start=before)
        card.update(self.root, 'T-1', failed)
        log = card.directory(self.root, 'T-1') / 'gates/M1-1.log'
        log.parent.mkdir()
        log.write_text('ERROR: fixture\nTraceback (most recent call last):\n  File "fixture.py", line 18\n    selected_repository()\nRuntimeError: example\n')
        card.record(self.root, 'T-1', 'gate', milestone='M1', status='failed',
                    head=git('rev-parse', 'HEAD', cwd=self.worktree).strip(), log=str(log))
        self.calls(('e1', None, DONE), ('rev', None, PASS),
                   ('e2', {'write': {'app.py': 'print(1)\n'}, 'commit': ['T-1 M2.1: print']}, DONE),
                   ('rev', None, PASS))
        def passed(*args, **kwargs):
            return dict(status='passed', head=git('rev-parse', 'HEAD', cwd=self.worktree).strip(), log=str(log))
        with patch('friday.gate.check', side_effect=passed):
            self.profile['gate'] = ['check']
            milestone.resume(self.root, 'T-1', self.profile)
        self.assertIn('selected_repository()', self.call(0)['prompt'])
        self.assertEqual(card.read(self.root, 'T-1')['milestones'][0]['start'], before)
        self.assertIn('def greet(n)', self.call(1)['prompt'])

    def test_without_a_gate_a_finished_task_is_unverified_everywhere(self):
        from friday import final, journal, status
        self.calls(("e1", {"write": {"greet.py": "def greet(n):\n    return n\n"}, "commit": ["T-1 M1.1: greet"]}, DONE),
                   ("rev", None, PASS),
                   ("e2", {"write": {"app.py": "print(1)\n"}, "commit": ["T-1 M2.1: print"]}, DONE),
                   ("rev", None, PASS))
        milestone.run_task(self.root, "T-1", self.profile)
        value = card.read(self.root, "T-1")
        self.assertEqual(value["state"], "done")
        self.assertEqual([item["verified"] for item in value["milestones"]], [False, False])
        self.assertEqual([entry["milestone"] for entry in value["unverified"]], ["M1", "M2"])
        self.assertIn("no gate is configured", value["unverified"][0]["reasons"][0])
        self.assertEqual(status.card_state_name(value), "done, not verified")
        self.assertIn("Not verified M1: no gate is configured", status.render_task(self.root, "T-1"))
        text = final.description(self.root, "T-1")
        self.assertIn("**M1. Функция** — done, not verified", text)
        self.assertIn("## Not verified", text)
        self.assertIn("M1: done but not verified", journal.describe({"type": "milestone_unverified", "milestone": "M1",
                                                                       "reasons": ["no gate is configured"]}))

    def test_checks_that_cannot_run_without_a_gate_block_the_milestone(self):
        broken = dict(DONE, checks="not_run", checks_note="vue-tsc падает: createGlobalTypesWriter is not a function")
        self.calls(("e1", {"write": {"greet.py": "x = 1\n"}, "commit": ["T-1 M1.1: greet"]}, broken))
        with self.assertRaises(FridayError) as error:
            milestone.run_task(self.root, "T-1", self.profile)
        self.assertEqual(error.exception.code, "environment_unavailable")
        first = card.read(self.root, "T-1")["milestones"][0]
        self.assertEqual((first["state"], first["blocked_reason"]), ("blocked", broken["checks_note"]))
        self.assertIn("milestone_blocked", [event["type"] for event in card.events(self.root, "T-1")])

    def test_failing_own_checks_without_a_gate_go_back_to_the_executor(self):
        failing = dict(DONE, checks="failed", checks_note="eslint: no-shadow в greet.py:1")
        self.calls(("e1", {"write": {"greet.py": "x = 1\n"}, "commit": ["T-1 M1.1: greet"]}, failing),
                   ("e1", {"write": {"greet.py": "x = 2\n"}, "commit": ["T-1 M1.1: fix"]}, DONE),
                   ("rev", None, PASS),
                   ("e2", {"write": {"app.py": "print(1)\n"}, "commit": ["T-1 M2.1: print"]}, DONE),
                   ("rev", None, PASS))
        milestone.run_task(self.root, "T-1", self.profile)
        self.assertIn("eslint: no-shadow в greet.py:1", self.call(1)["prompt"])
        self.assertEqual(self.call(2)["argv"][self.call(2)["argv"].index("--model") + 1], "big-1")

    def test_required_steps_reach_the_merge_request_and_the_pipeline_never_runs_them(self):
        from friday import final, status
        texts = []
        data = dict(json.loads(self.profile_path.read_text()),
                    steps={"bundle": {"scope": "task", "required": True, "run": ["true"]}})
        self.profile_path.write_text(json.dumps(data))
        selected = dict(self.profile, steps=data["steps"])
        with patch("friday.final.final_review", return_value=({}, {"model": "big-1"})), \
                patch("friday.steps.execute", side_effect=AssertionError("a step ran")), \
                patch("friday.final.deliver", side_effect=lambda root, key, profile: texts.append(final.description(root, key))):
            REAL_FINISH(self.root, "T-1", selected)
        self.assertIn("## Profile steps not done", texts[0])
        self.assertIn("- bundle — not run: `friday step bundle T-1`", texts[0])
        self.assertIn("Step not run: bundle — friday step bundle T-1", status.render_task(self.root, "T-1"))

    def test_final_fix_replaces_a_stale_frontier_cast_before_the_write_agent_runs(self):
        decision = 'Keep the approved runtime choice when fixing the final findings.'
        evidence = 'The controller observed the isolated gate pass on the recorded source.'
        card.update(self.root, 'T-1', lambda value: value['decisions'].append(
            dict(name='runtime', text=decision, reopen=[])))
        card.update(self.root, 'T-1', lambda value: value.update(controller_evidence={'summary': evidence}))
        self.profile["executor_tiers"] = ["strong", "efficient"]
        stale = models.public(dict(MODELS["big"], name="big"))
        card.update(self.root, "T-1", lambda value: value["cast"].update(develop=stale))
        self.calls(("fix", {"write": {"greet.py": "def greet(n):\n    return n or '?'\n"},
                            "commit": ["T-1 final: fix empty name"]}, DONE))
        reviews = [({"final-review": BLOCK}, stale), ({"final-review": PASS}, stale)]
        with patch("friday.final.final_review", side_effect=reviews), \
                patch("friday.final.deliver", return_value={"transport": "none"}), \
                patch("friday.runner.run", wraps=__import__("friday").runner.run) as spy:
            REAL_FINISH(self.root, "T-1", self.profile)
        writing = [call.args[0] for call in spy.call_args_list if call.args[0].write]
        self.assertEqual([spec.model for spec in writing], ["worker-1"])
        invoked = self.call(0)
        self.assertEqual(invoked["argv"][invoked["argv"].index("--model") + 1], "worker-1")
        self.assertIn("The final review of the whole task found blocking problems", invoked["prompt"])
        self.assertIn(decision, invoked['prompt'])
        self.assertIn(evidence, invoked['prompt'])
        writes = [event for event in card.events(self.root, "T-1")
                  if event["type"] == "run_finished" and event.get("role") == "develop"]
        self.assertEqual([(event["model"], event["tier"]) for event in writes], [("worker-1", "strong")])
        self.assertIn("return n or '?'", (Path(self.worktree) / "greet.py").read_text())

    def test_write_role_rejects_forbidden_or_spoofed_executors_before_admission(self):
        self.profile["executor_tiers"] = ["strong", "efficient"]
        frontier = models.public(dict(MODELS["big"], name="big"))
        worker = models.public(dict(MODELS["worker"], name="worker"))
        initial_events = len(card.events(self.root, "T-1"))
        for entry in (frontier, dict(frontier, tier="strong"), dict(worker, model=frontier["model"]),
                      dict(worker, name="unknown")):
            with self.subTest(entry=entry), patch("friday.card.run_agent") as admitted, \
                    patch("friday.runner.run") as started, self.assertRaises(FridayError) as error:
                milestone.run_role(self.root, "T-1", self.profile, "develop", entry, self.worktree,
                                   "implement", milestone.EXECUTOR_SCHEMA, "M1", write=True)
            self.assertEqual(error.exception.code, "cast_impossible")
            admitted.assert_not_called()
            started.assert_not_called()
        self.assertEqual(len(card.events(self.root, "T-1")), initial_events)

    def test_unverified_reasons(self):
        ui = {"ui": True}
        self.assertEqual(milestone.unverified(ui, {"checks": "not_run", "checks_note": "n", "browser": {"status": "checked"}},
                                              {"status": "passed"}), [])
        reasons = milestone.unverified(ui, {"checks": "failed", "checks_note": "tsc", "browser": {"status": "skipped", "reason": "нет preview"}},
                                       {"status": "failed"})
        self.assertEqual(reasons, ["the executor's checks failed: tsc", "browser acceptance did not run: нет preview"])

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
        self.assertTrue(any("two failures in a row" in note for note in notes))
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
        cli = Path(__file__).resolve().parents[1] / "bin" / "friday"
        result = subprocess.run([sys.executable, str(cli), "decide", "T-1", "early", "Before the run.", "--reopen", "M1"],
                                capture_output=True, text=True, env=dict(os.environ, FRIDAY_HOME=str(self.root)))
        self.assertEqual(result.returncode, 0, result.stderr)  # a milestone that has not run is not reopened
        self.assertIn("Only added to the context of milestones (M1, M2)", result.stdout)
        self.assertNotIn("Reopened", result.stdout)
        self.assertEqual(card.events(self.root, "T-1")[-1]["reopened"], [])
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
        self.assertEqual(len(value["decisions"]), 3)
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

    def project_execution(self, **limits):
        self.profile.update(toolset={"execute": "project"}, project={"plan_file": "ACTUAL_PLAN.md", "execute_command": "/execute-plan"},
                            **({"limits": limits} if limits else {}))
        self.profile_path.write_text(json.dumps({k: v for k, v in self.profile.items() if k != "path"}))

    STAGES = {"steps": [{"write": {"greet.py": "def greet(n):\n    return n\n"}, "message": "M1.1: greet"},
                        {"write": {"app.py": "print(1)\n"}, "message": "M2.1: print"}]}
    FINISHED = {"summary": "Готово.", "questions": []}

    def test_project_execution_runs_the_project_command_and_closes_milestones_from_its_commits(self):
        self.project_execution()
        self.calls(("x1", self.STAGES, self.FINISHED))
        milestone.run_task(self.root, "T-1", self.profile)
        value = card.read(self.root, "T-1")
        self.assertEqual(value["state"], "done")
        self.assertEqual([item["state"] for item in value["milestones"]], ["done", "done"])
        self.assertTrue(all(stage["commit"] for item in value["milestones"] for stage in item["stages"]))
        self.assertEqual((self.fake / "counter").read_text(), "1")  # the project's process reviews its stages itself
        call = self.call(0)
        self.assertTrue(call["prompt"].startswith("/execute-plan\n"))
        self.assertIn("- M1.1 (M1 Функция): greet", call["prompt"])
        self.assertEqual(call["argv"][call["argv"].index("--permission-mode") + 1], "auto")
        self.assertEqual(call["argv"][call["argv"].index("--model") + 1], "worker-1")
        self.assertEqual(value["execute_session"], "x1")

    def test_project_execution_question_stops_the_task_and_the_answer_continues_its_session(self):
        from friday import status
        self.project_execution()
        self.calls(("x1", None, {"summary": "", "questions": ["Маску расширять?"]}))
        result = milestone.run_task(self.root, "T-1", self.profile)
        value = card.read(self.root, "T-1")
        self.assertEqual((result["questions"], value["state"], value["questions"]), (["Маску расширять?"], "stopped", ["Маску расширять?"]))
        self.assertIn("Question for the owner: Маску расширять?", status.render_task(self.root, "T-1"))
        card.update(self.root, "T-1", lambda current: current.update(pending_answer="Да, до 12 цифр"))
        self.calls(("x1", self.STAGES, self.FINISHED))
        milestone.run_task(self.root, "T-1", self.profile)
        call = self.call(0)
        self.assertEqual(call["argv"][call["argv"].index("--resume") + 1], "x1")
        self.assertIn("Да, до 12 цифр", call["prompt"])
        self.assertIn("- Маску расширять?", call["prompt"])  # what stopped the run goes back with the answer
        value = card.read(self.root, "T-1")
        self.assertEqual(value["state"], "done")
        self.assertNotIn("questions", value)
        self.assertNotIn("pending_answer", value)

    def test_project_execution_over_the_limit_without_a_stage_commit_or_with_leftovers_asks_the_owner(self):
        self.project_execution(stage_lines=1)
        self.calls(("x1", {"steps": self.STAGES["steps"][:1]}, self.FINISHED))
        result = milestone.run_task(self.root, "T-1", self.profile)
        self.assertTrue(any("M1.1: 1 files and 2 added lines" in q for q in result["questions"]), result["questions"])
        self.assertTrue(any("M2.1 has no commit" in q for q in result["questions"]), result["questions"])
        self.assertEqual([item["state"] for item in card.read(self.root, "T-1")["milestones"]], ["pending", "pending"])
        self.calls(("x1", self.STAGES["steps"][1:] and {"steps": self.STAGES["steps"][1:]}, self.FINISHED))
        milestone.resume(self.root, "T-1", self.profile)  # resume continues the stopped session with Friday's findings
        self.assertEqual(self.call(0)["argv"][self.call(0)["argv"].index("--resume") + 1], "x1")
        self.assertIn("M2.1 has no commit", self.call(0)["prompt"])
        self.calls(("x2", {"write": {"note.txt": "draft\n"}}, self.FINISHED))
        card.update(self.root, "T-1", lambda current: current.update(execute_session=None))
        result = milestone.run_task(self.root, "T-1", self.profile)
        self.assertIn("uncommitted changes", result["questions"][0])

    def test_projectResume_afterEnvironmentFailure_repeatsChecksWithoutWakingTheSession(self):
        self.project_execution()
        self.profile.update(gate=["check"], gate_image="img")
        outcomes = iter(["environment_unavailable", "passed"])
        def fake_gate(root, profile, worktree, base, log, on_wait=None):
            return {"status": next(outcomes), "head": "h", "log": str(log), "exit_code": 0, "unchanged": True}
        self.calls(("x1", self.STAGES, self.FINISHED))
        with patch("friday.milestone.gate.check", side_effect=fake_gate):
            with self.assertRaises(FridayError):
                milestone.run_task(self.root, "T-1", self.profile)
            self.calls()
            milestone.resume(self.root, "T-1", self.profile)
        self.assertFalse((self.fake / "counter").exists())  # no session turn: it had finished and nothing asked it for more
        value = card.read(self.root, "T-1")
        self.assertEqual([item["state"] for item in value["milestones"]], ["done", "done"])
        self.assertEqual(value["milestones"][0]["summary"], "Готово.")

    def test_projectRun_staleGateImage_stopsForOwnerStepAndResumeKeepsTheSession(self):
        from friday import status
        self.project_execution()
        self.profile.update(gate=["check"], gate_image="img", gate_image_step="gate-image",
                            steps={"gate-image": {"scope": "profile", "run": ["build.sh"]}})
        outcomes = iter(["image_stale", "passed"])
        def fake_gate(root, profile, worktree, base, log, on_wait=None):
            return {"status": next(outcomes), "head": "h", "log": str(log), "exit_code": 125, "unchanged": True}
        self.calls(("x1", self.STAGES, self.FINISHED))
        with patch("friday.milestone.gate.check", side_effect=fake_gate):
            with self.assertRaises(FridayError) as error:
                milestone.run_task(self.root, "T-1", self.profile)
            self.assertEqual(error.exception.code, "image_stale")
            value = card.read(self.root, "T-1")
            self.assertEqual(value["state"], "stopped")
            self.assertIn("friday step gate-image --profile " + str(self.profile_path), value["owner_step"])
            self.assertNotIn("questions", value)  # the owner's step is not a question for the session
            self.assertIn("Owner step needed:", status.render_task(self.root, "T-1"))
            self.calls()
            milestone.resume(self.root, "T-1", self.profile)
        self.assertFalse((self.fake / "counter").exists())
        value = card.read(self.root, "T-1")
        self.assertEqual([item["state"] for item in value["milestones"]], ["done", "done"])
        self.assertNotIn("owner_step", value)

    def test_runTask_staleGateImageWithoutBuildStep_namesTheMissingStep(self):
        self.profile.update(gate=["check"], gate_image="img")
        def fake_gate(root, profile, worktree, base, log, on_wait=None):
            return {"status": "image_stale", "head": "h", "log": str(log), "exit_code": 125, "unchanged": True}
        self.calls(("e1", {"write": {"greet.py": "x = 1\n"}, "commit": ["T-1 M1.1: greet"]}, DONE))
        with patch("friday.milestone.gate.check", side_effect=fake_gate), self.assertRaises(FridayError) as error:
            milestone.run_task(self.root, "T-1", self.profile)
        self.assertIn("the profile has no image build step", str(error.exception))
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["milestones"][0]["state"]), ("stopped", "blocked"))

    def test_decide_cli_passes_the_answer_to_the_execution_session_in_the_background(self):
        import time
        self.project_execution()
        self.profile["project"]["session_env"] = ["FAKE_DIR"]  # the background run has no patched model environment
        self.profile_path.write_text(json.dumps({k: v for k, v in self.profile.items() if k != "path"}))
        self.calls(("x1", None, {"summary": "", "questions": ["Маску расширять?"]}))
        milestone.run_task(self.root, "T-1", self.profile)
        self.calls(("x1", self.STAGES, self.FINISHED))
        cli = Path(__file__).resolve().parents[1] / "bin" / "friday"
        result = subprocess.run([sys.executable, str(cli), "decide", "T-1", "mask", "Yes"], capture_output=True, text=True,
                                env=dict(os.environ, FRIDAY_HOME=str(self.root)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("The answer is passed to the execution session", result.stdout)
        deadline = time.time() + 60
        while card.read(self.root, "T-1")["state"] not in ("done", "failed") and time.time() < deadline:
            time.sleep(0.5)
        # The final phase that follows has its own tests; here it runs unpatched and may fail on the fake agent.
        value = card.read(self.root, "T-1")
        self.assertEqual([item["state"] for item in value["milestones"]], ["done", "done"],
                         (card.directory(self.root, "T-1") / "run.log").read_text())
        self.assertIn("Yes", self.call(0)["prompt"])
        self.assertIn("final_started", [event["type"] for event in card.events(self.root, "T-1")])

    def test_running_needs_an_approved_plan(self):
        card.update(self.root, "T-1", lambda value: value.update(state="awaiting_approval", plan=dict(value["plan"], approved=False)))
        with self.assertRaises(FridayError):
            milestone.run_task(self.root, "T-1", self.profile)


class ProjectStageSourcesTests(unittest.TestCase):
    def repo(self, name, files):
        path = Path(self.tmp.name) / name
        path.mkdir()
        git("init", "-q", "-b", "main", cwd=path)
        git("commit", "-q", "--allow-empty", "-m", "base", cwd=path)
        base = git("rev-parse", "HEAD", cwd=path).strip()
        for message, written in files:
            for file, text in written.items():
                (path / file).write_text(text)
            git("add", "-A", cwd=path)
            git("commit", "-q", "-m", message, cwd=path)
        return str(path), base

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_stage_committed_in_the_showcase_counts_with_its_stories_paths(self):
        kit, kit_base = self.repo("kit", [("S1: kit", {"Bar.vue": "a\n"})])
        showcase, showcase_base = self.repo("showcase", [("S2: story", {"Bar.stories.ts": "a\nb\n"})])
        document = {"milestones": [{"id": "M1", "files": ["Bar.vue"], "stages": [{"id": "S1"}]},
                                   {"id": "M2", "files": ["stories:Bar.stories.ts"], "stages": [{"id": "S2"}]}]}
        kit_only = [(kit, kit_base, "")]
        self.assertEqual(milestone.project_size(kit_only, document), ["Stage S2 has no commit with \"S2:\" in its subject."])
        sources = milestone.stage_sources({"worktree": kit, "base": kit_base,
                                           "stories": {"worktree": showcase, "base": showcase_base}})
        self.assertEqual(milestone.project_size(sources, document), [])
        self.assertEqual(milestone.project_size(sources, document, {"stage_lines": 1}),
                         ["Stage S2: 1 files and 2 added lines over the limits of %d and 1." % plan.LIMITS["stage_files"]])
        self.assertEqual([prefix for _, _, prefix in milestone.source_commits(sources, "S2")], ["stories:"])


if __name__ == "__main__":
    unittest.main()
