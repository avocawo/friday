import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, plan, workspace
from friday.errors import FridayError

CLI = Path(__file__).resolve().parents[1] / "bin" / "friday"

# Each call of the fake agent takes the next entry of calls.json: the structured answer and the session it reports.
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
directory = os.environ["FAKE_DIR"]
prompt = sys.stdin.read()
counter = os.path.join(directory, "counter")
index = int(open(counter).read()) if os.path.exists(counter) else 0
open(counter, "w").write(str(index + 1))
with open(os.path.join(directory, "call-%d.json" % index), "w") as handle:
    json.dump({"argv": sys.argv[1:], "prompt": prompt}, handle)
call = json.load(open(os.path.join(directory, "calls.json")))[index]
print(json.dumps({"type": "system", "subtype": "init", "session_id": call["session"]}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": call["session"], "result": "",
                  "structured_output": call["answer"], "total_cost_usd": 0.01, "num_turns": 1,
                  "usage": {"input_tokens": 10, "output_tokens": 10}}), flush=True)
'''

MODELS = {
    "big": {"model": "big-1", "vendor": "alpha", "driver": "claude", "tier": "frontier"},
    "rival": {"model": "rival-1", "vendor": "beta", "driver": "claude", "tier": "frontier"},
    "worker": {"model": "worker-1", "vendor": "gamma", "driver": "claude", "tier": "strong"},
}

PLAN = {
    "summary": "Добавить приветствие.", "rules": ["Тесты рядом с кодом (AGENTS.md)"], "assumptions": ["Язык — русский."],
    "questions": [], "responses": [],
    "milestones": [
        {"id": "M1", "title": "Модель", "goal": "Функция приветствия.", "complexity": "clear", "ui": False, "parallel": False,
         "depends_on": [], "files": ["app.py", "greet.py"],
         "stages": [{"id": "M1.1", "brief": "Функция greet", "steps": ["написать greet"], "files": ["app.py"],
                     "new_files": ["greet.py"], "estimate_lines": 40,
                     "criteria": [{"text": "greet('A') возвращает 'Привет, A'", "check": "code"}]}]},
        {"id": "M2", "title": "Экран", "goal": "Показать приветствие.", "complexity": "complex", "ui": True, "parallel": False,
         "depends_on": ["M1"], "files": ["page.html"],
         "stages": [{"id": "M2.1", "brief": "Вывести на страницу", "steps": [], "files": ["page.html"], "new_files": [],
                     "estimate_lines": 20, "criteria": [{"text": "На странице видно «Привет»", "check": "behavior"}]}]},
    ],
}
REVIEW = {"verdict": "changes", "findings": [
    {"milestone": "M1", "stage": "M1.1", "severity": "blocking", "problem": "Нет теста на пустое имя.", "suggestion": "Добавить критерий."},
    {"milestone": "M2", "stage": "M2.1", "severity": "minor", "problem": "Лучше вынести текст в шаблон.", "suggestion": "Шаблон."},
]}


def revised():
    value = copy.deepcopy(PLAN)
    value["milestones"][0]["stages"][0]["criteria"].append({"text": "greet('') бросает ValueError", "check": "code"})
    value["responses"] = [{"finding": 0, "accepted": True, "reason": "Добавлен критерий."},
                          {"finding": 1, "accepted": False, "reason": "Шаблонов в проекте нет, см. page.html."}]
    return value


def git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root, self.fake = base / "home", base / "fake"
        (self.fake / "bin").mkdir(parents=True)
        (self.fake / "bin" / "claude").write_text(FAKE)
        (self.fake / "bin" / "claude").chmod(0o755)
        origin, self.repo = base / "origin.git", base / "repo"
        git("init", "-q", "--bare", "-b", "main", str(origin))
        git("clone", "-q", str(origin), str(self.repo))
        (self.repo / "app.py").write_text("print('hi')\n")
        (self.repo / "page.html").write_text("<p></p>\n")
        git("add", ".", cwd=self.repo)
        git("commit", "-qm", "init", cwd=self.repo)
        git("push", "-q", "origin", "HEAD:main", cwd=self.repo)
        self.profile_path = base / "profile.json"
        self.profile = {"name": "demo", "repository": str(self.repo), "base_branch": "main", "worktree_root": str(base / "worktrees"),
                        "models": MODELS, "roles": {"plan": ["big"], "plan-review": ["rival"], "develop": ["worker"]}}
        self.profile_path.write_text(json.dumps(self.profile))
        self.profile["path"] = str(self.profile_path)
        environment = patch.dict(os.environ, {"PATH": str(self.fake / "bin") + os.pathsep + os.environ.get("PATH", ""),
                                              "FAKE_DIR": str(self.fake)})
        environment.start()
        self.addCleanup(environment.stop)
        patcher = patch("friday.models.environment", return_value={"FAKE_DIR": str(self.fake)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def calls(self, *answers):
        (self.fake / "calls.json").write_text(json.dumps([{"session": session, "answer": answer} for session, answer in answers]))
        (self.fake / "counter").unlink(missing_ok=True)

    def call(self, index):
        return json.loads((self.fake / ("call-%d.json" % index)).read_text())

    def start(self, key="T-1"):
        card.create(self.root, key, str(self.profile_path))
        workspace.create(self.root, key, self.profile)
        return plan.make(self.root, key, self.profile, "Сделать приветствие для пользователя.")

    def test_plan_is_reviewed_revised_and_waits_for_approval(self):
        self.calls(("planner", PLAN), ("reviewer", REVIEW), ("planner", revised()))
        document = self.start()
        value = card.read(self.root, "T-1")
        self.assertEqual(value["state"], "awaiting_approval")
        self.assertEqual({role: entry["name"] for role, entry in value["cast"].items()}["plan-review"], "rival")
        self.assertEqual([item["id"] for item in value["milestones"]], ["M1", "M2"])
        self.assertEqual(len(document["milestones"][0]["stages"][0]["criteria"]), 2)
        self.assertEqual([note["problem"] for note in value["plan"]["review_notes"]], ["Лучше вынести текст в шаблон."])
        self.assertIn("Шаблонов в проекте нет", value["plan"]["review_notes"][0]["answer"])
        planner, reviewer, revision = self.call(0), self.call(1), self.call(2)
        self.assertEqual(planner["argv"][planner["argv"].index("--model") + 1], "big-1")
        self.assertEqual(reviewer["argv"][reviewer["argv"].index("--model") + 1], "rival-1")
        self.assertIn("Сделать приветствие", reviewer["prompt"])
        self.assertEqual(revision["argv"][revision["argv"].index("--resume") + 1], "planner")
        self.assertIn("1. [minor] M2/M2.1", revision["prompt"])
        self.assertIn("--json-schema", planner["argv"])
        text = plan.render(self.root, "T-1")
        for expected in ("### M2. Экран (сложная, интерфейс) после M1", "Несогласованные замечания", "[браузер]", "plan-review → rival-1"):
            self.assertIn(expected, text)
        self.assertTrue((Path(value["worktree"]) / "app.py").is_file())
        plan.approve(self.root, "T-1")
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["plan"]["approved"]), ("approved", True))
        with self.assertRaises(FridayError):
            plan.approve(self.root, "T-1")

    def test_infeasible_plan_goes_back_to_the_planner_once(self):
        broken = copy.deepcopy(PLAN)
        broken["milestones"][0]["stages"][0]["criteria"].append({"text": "Видно в браузере", "check": "behavior"})
        clean = {"verdict": "ok", "findings": []}
        self.calls(("planner", broken), ("planner", PLAN), ("reviewer", clean))
        self.start()
        self.assertIn("not marked ui", self.call(1)["prompt"])
        self.assertEqual(self.call(1)["argv"][self.call(1)["argv"].index("--resume") + 1], "planner")
        self.assertEqual([event["type"] for event in card.events(self.root, "T-1")].count("plan_invalid"), 1)
        self.calls(("planner", broken), ("planner", broken))
        with self.assertRaises(FridayError) as error:
            self.start("T-2")
        self.assertEqual(error.exception.code, "plan_invalid")

    def test_feasibility_checks(self):
        worktree = self.repo
        def problems(change):
            value = copy.deepcopy(PLAN)
            change(value)
            return " | ".join(plan.validate(value, worktree))
        self.assertEqual(plan.validate(PLAN, worktree), [])
        self.assertIn("Duplicate id M1", problems(lambda v: v["milestones"][1].update(id="M1", depends_on=[])))
        self.assertIn("not an earlier milestone", problems(lambda v: v["milestones"][0].update(depends_on=["M2"])))
        self.assertIn("missing from milestone", problems(lambda v: v["milestones"][0].update(files=["app.py"])))
        self.assertIn("limit is 500", problems(lambda v: v["milestones"][0]["stages"][0].update(estimate_lines=900)))
        self.assertIn("do not exist: nope.py", problems(lambda v: (v["milestones"][0]["stages"][0]["files"].append("nope.py"),
                                                                   v["milestones"][0]["files"].append("nope.py"))))
        self.assertIn("already exist: page.html", problems(lambda v: (v["milestones"][0]["stages"][0]["new_files"].append("page.html"),
                                                                      v["milestones"][0]["files"].append("page.html"))))
        self.assertIn("no behavior criterion", problems(lambda v: v["milestones"][1]["stages"][0]["criteria"][0].update(check="code")))
        self.assertIn("unsafe path", problems(lambda v: (v["milestones"][0]["stages"][0]["new_files"].append("../x.py"),
                                                         v["milestones"][0]["files"].append("../x.py"))))
        def parallel(value):
            value["milestones"][1].update(depends_on=[], parallel=True, files=["page.html", "app.py"])
            value["milestones"][1]["stages"][0]["files"].append("app.py")
        self.assertIn("is parallel but shares files with M1: app.py", problems(parallel))
        self.assertIn("limit is 3", " ".join(plan.validate(PLAN, worktree, {"stage_lines": 30, "stage_files": 3}) +
                                             plan.validate(dict(PLAN, milestones=[dict(PLAN["milestones"][0], files=list("abcd"),
                                             stages=[dict(PLAN["milestones"][0]["stages"][0], new_files=list("abc"))])]), worktree,
                                             {"stage_files": 3})))

    def test_overlap_with_another_active_task_is_a_warning(self):
        clean = {"verdict": "ok", "findings": []}
        self.calls(("p1", PLAN), ("r1", clean))
        self.start("T-1")
        self.calls(("p2", PLAN), ("r2", clean))
        self.start("T-2")
        overlaps = card.read(self.root, "T-2")["plan"]["overlaps"]
        self.assertEqual(len(overlaps), 1)
        self.assertIn("T-1", overlaps[0])
        self.assertIn("app.py", overlaps[0])

    def test_workspace_is_created_once(self):
        card.create(self.root, "T-1", str(self.profile_path))
        first = workspace.create(self.root, "T-1", self.profile)
        self.assertEqual(workspace.create(self.root, "T-1", self.profile), first)
        card.update(self.root, "T-1", lambda value: value.update(worktree=None))
        self.assertEqual(workspace.create(self.root, "T-1", self.profile), first)  # registered path is reused
        self.assertEqual(subprocess.run(["git", "-C", first, "branch", "--show-current"], capture_output=True, text=True).stdout.strip(),
                         "friday/T-1")

    def test_worktree_setup_runs_in_a_new_worktree_and_failures_stop_the_start(self):
        self.profile["worktree_setup"] = [["sh", "-c", "[ -e deps ] || cp -R {repository}/app.py deps"]]
        card.create(self.root, "T-1", str(self.profile_path))
        worktree = Path(workspace.create(self.root, "T-1", self.profile))
        self.assertEqual((worktree / "deps").read_text(), "print('hi')\n")
        self.profile["worktree_setup"] = [["false"]]
        card.create(self.root, "T-2", str(self.profile_path))
        with self.assertRaises(FridayError) as error:
            workspace.create(self.root, "T-2", self.profile)
        self.assertEqual(error.exception.code, "worktree_setup_failed")
        self.assertIsNone(card.read(self.root, "T-2").get("worktree"))

    def test_start_command_plans_from_a_brief(self):
        clean = {"verdict": "ok", "findings": []}
        self.calls(("p", PLAN), ("r", clean))
        brief = Path(self.directory.name) / "brief.md"
        brief.write_text("Сделать приветствие.")
        environment = dict(os.environ, FRIDAY_HOME=str(self.root))
        script = ("import sys, runpy; from unittest.mock import patch; "
                  "patch('friday.models.environment', return_value={'FAKE_DIR': %r}).start(); "
                  "sys.argv = ['friday', 'start', 'T-9', '--profile', %r, '--brief', %r]; "
                  "runpy.run_path(%r, run_name='__main__')") % (str(self.fake), str(self.profile_path), str(brief), str(CLI))
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=environment,
                                cwd=str(Path(__file__).resolve().parents[1]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("# План T-9", result.stdout)
        self.assertIn("friday approve T-9", result.stdout)
        result = subprocess.run([sys.executable, str(CLI), "approve", "T-9", "--no-run"], capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(card.read(self.root, "T-9")["state"], "approved")

    def test_auto_approval_skips_the_owner(self):
        self.calls(("p", PLAN), ("r", {"verdict": "ok", "findings": []}))
        self.profile_path.write_text(json.dumps(dict(json.loads(self.profile_path.read_text()), approval="auto")))
        brief = Path(self.directory.name) / "brief.md"
        brief.write_text("Сделать приветствие.")
        script = ("import sys, runpy; from unittest.mock import patch; "
                  "patch('friday.models.environment', return_value={'FAKE_DIR': %r}).start(); "
                  "sys.argv = ['friday', 'start', 'T-8', '--profile', %r, '--brief', %r, '--no-run']; "
                  "runpy.run_path(%r, run_name='__main__')") % (str(self.fake), str(self.profile_path), str(brief), str(CLI))
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                env=dict(os.environ, FRIDAY_HOME=str(self.root)), cwd=str(Path(__file__).resolve().parents[1]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("План утверждён автоматически", result.stdout)
        self.assertEqual(card.read(self.root, "T-8")["state"], "approved")
        self.assertEqual(card.events(self.root, "T-8")[-1], dict(card.events(self.root, "T-8")[-1], type="plan_approved", by="auto"))


if __name__ == "__main__":
    unittest.main()
