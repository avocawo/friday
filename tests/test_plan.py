import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import models, card, milestone, plan, workspace
from friday.errors import FridayError

CLI = Path(__file__).resolve().parents[1] / "bin" / "friday"

# Each call of the fake agent takes the next entry of calls.json: the structured answer and the session it reports.
FAKE = r'''#!/usr/bin/env python3
import json, os, sys

os.environ["FRIDAY_LANG"] = "en"  # assertions read Friday's English messages
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
        self.assertNotIn("--resume", revision["argv"])  # a revision starts a new session with the plan in the prompt
        self.assertIn("1. [minor] M2/M2.1", revision["prompt"])
        self.assertIn("Your plan:", revision["prompt"])
        self.assertIn("Функция greet", revision["prompt"])
        self.assertIn("Сделать приветствие", revision["prompt"])
        self.assertIn("--json-schema", planner["argv"])
        text = plan.render(self.root, "T-1")
        for expected in ("### M2. Экран (complex, interface) after M1", "Open review notes", "[browser]", "plan-review → rival-1"):
            self.assertIn(expected, text)
        self.assertTrue((Path(value["worktree"]) / "app.py").is_file())
        plan.approve(self.root, "T-1")
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["plan"]["approved"]), ("approved", True))
        with self.assertRaises(FridayError):
            plan.approve(self.root, "T-1")

    def test_failed_start_keeps_the_plan_and_replaces_a_reviewer_out_of_quota(self):
        self.profile["models"] = dict(MODELS, judge={"model": "judge-1", "vendor": "delta", "driver": "claude", "tier": "frontier"})
        real = plan.agent
        def quota_once(root, key, profile, role, *rest, **options):
            if role == "plan-review" and profile.get("quota_hit") is None:
                profile["quota_hit"] = True
                models.exhausted(root, "beta", "usage limit")
                raise FridayError("agent_failed", "plan-review did not return a result (quota): usage limit")
            return real(root, key, profile, role, *rest, **options)
        self.calls(("planner", PLAN), ("judge", {"verdict": "ok", "findings": []}))
        with patch("friday.plan.agent", side_effect=quota_once):
            self.start()
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["cast"]["plan-review"]["model"]), ("awaiting_approval", "judge-1"))
        self.calls(("judge2", {"verdict": "ok", "findings": []}))
        card.update(self.root, "T-1", lambda v: v.update(state="failed"))
        plan.make(self.root, "T-1", self.profile, "Сделать приветствие для пользователя.")  # the rerun does not plan again
        self.assertIn("plan_reused", [event["type"] for event in card.events(self.root, "T-1")])

    def test_owner_sends_a_waiting_plan_back_with_a_decision(self):
        clean = {"verdict": "ok", "findings": []}
        self.calls(("planner", PLAN), ("reviewer", clean))
        self.start()
        with self.assertRaises(FridayError) as error:
            plan.replan(self.root, "T-1", self.profile)  # nothing new to plan from
        self.assertEqual(error.exception.code, "invalid_state")
        self.profile["approval"] = "auto"  # the owner looks at a revised plan even when the first one needed nobody
        milestone.decide(self.root, "T-1", "scope", "Drop the page milestone; the brief asks for the function only.")
        smaller = copy.deepcopy(PLAN)
        smaller["milestones"] = smaller["milestones"][:1]
        self.calls(("planner-2", smaller), ("reviewer-2", clean))
        document = plan.replan(self.root, "T-1", self.profile)
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["plan"]["approved"], value["plan"]["revision"]), ("awaiting_approval", False, 2))
        self.assertEqual([item["id"] for item in document["milestones"]], ["M1"])
        self.assertEqual([item["id"] for item in value["milestones"]], ["M1"])
        self.assertEqual([item["name"] for item in value["decisions"]], ["scope"])
        planner, reviewer = self.call(0), self.call(1)
        self.assertNotIn("--resume", planner["argv"])
        self.assertEqual(planner["argv"][planner["argv"].index("--model") + 1], "big-1")
        for expected in ("Your plan:", "Вывести на страницу", "sent it back", "- scope: Drop the page milestone", "Сделать приветствие"):
            self.assertIn(expected, planner["prompt"])
        self.assertEqual(reviewer["argv"][reviewer["argv"].index("--model") + 1], "rival-1")
        self.assertIn("Drop the page milestone", reviewer["prompt"])
        types = [event["type"] for event in card.events(self.root, "T-1")]
        self.assertEqual(types.count("plan_ready"), 2)
        self.assertEqual(types.count("run_finished"), 4)  # the first plan's runs and costs are kept
        self.assertLess(types.index("replan_started"), len(types) - 1 - types[::-1].index("plan_ready"))
        started = next(event for event in card.events(self.root, "T-1") if event["type"] == "replan_started")
        self.assertEqual((started["revision"], started["decisions"]), (1, ["scope"]))
        with self.assertRaises(FridayError):
            plan.replan(self.root, "T-1", self.profile)  # the decision is in the plan now
        plan.approve(self.root, "T-1")
        with self.assertRaises(FridayError) as error:
            plan.replan(self.root, "T-1", self.profile)
        self.assertEqual(error.exception.code, "invalid_state")

    def test_failed_replan_keeps_the_waiting_plan(self):
        self.calls(("planner", PLAN), ("reviewer", {"verdict": "ok", "findings": []}))
        self.start()
        before = (Path(card.directory(self.root, "T-1")) / "plan.json").read_text()
        milestone.decide(self.root, "T-1", "size", "Keep every stage under 100 lines.")
        broken = copy.deepcopy(PLAN)
        broken["milestones"][0]["stages"][0]["files"].append("missing.py")
        self.calls(("p", broken), ("p", broken))
        with self.assertRaises(FridayError) as error:
            plan.replan(self.root, "T-1", self.profile)
        self.assertEqual(error.exception.code, "plan_invalid")
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["plan"]["revision"]), ("awaiting_approval", 1))
        self.assertEqual((Path(card.directory(self.root, "T-1")) / "plan.json").read_text(), before)
        self.assertEqual(card.events(self.root, "T-1")[-1]["type"], "replan_failed")
        self.calls(("p", PLAN), ("r", {"verdict": "ok", "findings": []}))
        plan.replan(self.root, "T-1", self.profile)  # the decision still counts as new
        self.assertEqual(card.read(self.root, "T-1")["plan"]["revision"], 2)

    def test_infeasible_plan_goes_back_to_the_planner_once(self):
        broken = copy.deepcopy(PLAN)
        broken["milestones"][0]["stages"][0]["criteria"].append({"text": "Видно в браузере", "check": "behavior"})
        clean = {"verdict": "ok", "findings": []}
        self.calls(("planner", broken), ("planner", PLAN), ("reviewer", clean))
        self.start()
        self.assertIn("not marked ui", self.call(1)["prompt"])
        self.assertNotIn("--resume", self.call(1)["argv"])
        self.assertEqual([event["type"] for event in card.events(self.root, "T-1")].count("plan_invalid"), 1)
        self.calls(("planner", broken), ("planner", broken))
        with self.assertRaises(FridayError) as error:
            self.start("T-2")
        self.assertEqual(error.exception.code, "plan_invalid")

    def test_rules_digest_is_made_once_per_document_content_and_replaces_reading(self):
        (self.repo / "AGENTS.md").write_text("Тесты рядом с кодом.\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "GUIDE.md").write_text("Слои по FSD.\n")
        (self.repo / "docs" / "ideas").mkdir()
        (self.repo / "docs" / "ideas" / "one.md").write_text("Идея задачи.\n")
        git("add", ".", cwd=self.repo)
        git("commit", "-qm", "docs", cwd=self.repo)
        git("push", "-q", "origin", "HEAD:main", cwd=self.repo)
        digest = {"rules": ["Тесты рядом с кодом (AGENTS.md)"], "documents": [{"path": "docs/GUIDE.md", "covers": "слои"}]}
        clean = {"verdict": "ok", "findings": []}
        self.calls(("d", digest), ("p1", PLAN), ("r1", clean))
        self.start("T-1")
        maker, planner, reviewer = self.call(0), self.call(1), self.call(2)
        self.assertIn("AGENTS.md, docs/GUIDE.md.", maker["prompt"])
        self.assertNotIn("ideas", maker["prompt"])
        self.assertEqual(maker["argv"][maker["argv"].index("--model") + 1], "big-1")
        for prompt in (planner["prompt"], reviewer["prompt"]):
            self.assertIn("Rules digest (from AGENTS.md, docs/GUIDE.md):\n- Тесты рядом с кодом (AGENTS.md)", prompt)
            self.assertIn("- docs/GUIDE.md: слои", prompt)
        self.assertIn("Do not read those documents whole", planner["prompt"])
        self.assertIn("digest_made", [event["type"] for event in card.events(self.root, "T-1")])
        self.calls(("p2", PLAN), ("r2", clean))  # same documents: no digest run
        self.start("T-2")
        self.assertIn("Rules digest", self.call(0)["prompt"])
        self.assertIn("digest_reused", [event["type"] for event in card.events(self.root, "T-2")])
        self.assertEqual(plan.rules_sources(dict(self.profile, rules_docs=["docs/**/*.md"]), self.repo), ["docs/GUIDE.md", "docs/ideas/one.md"])

    def test_repository_map_reaches_the_planner_and_the_reviewer_gets_the_files_of_the_plan(self):
        (self.repo / ".gitignore").write_text("node_modules/\n")
        (self.repo / "node_modules").mkdir()
        (self.repo / "node_modules" / "dep.js").write_text("x\n")
        git("add", ".", cwd=self.repo)
        git("commit", "-qm", "ignore", cwd=self.repo)
        git("push", "-q", "origin", "HEAD:main", cwd=self.repo)
        self.profile["repo_map"] = "builtin"
        clean = {"verdict": "ok", "findings": []}
        self.calls(("p1", PLAN), ("r1", clean))
        self.start("T-1")
        planner, reviewer = self.call(0), self.call(1)
        self.assertIn("Repository map (tracked files: lines):\n.gitignore (1)\napp.py (1)\npage.html (1)", planner["prompt"])
        self.assertNotIn("node_modules/dep.js", planner["prompt"])
        self.assertIn("Use the repository map below", planner["prompt"])
        self.assertIn("--- app.py\nprint('hi')", reviewer["prompt"])
        self.assertIn("--- page.html\n<p></p>", reviewer["prompt"])
        self.assertNotIn("--mcp-config", planner["argv"])
        self.calls(("p2", PLAN), ("r2", clean))
        self.start("T-2")
        self.assertIn("map_reused", [event["type"] for event in card.events(self.root, "T-2")])
        self.profile["repo_map"] = "none"
        self.calls(("p3", PLAN), ("r3", clean))
        self.start("T-3")
        self.assertNotIn("Repository map", self.call(0)["prompt"])
        self.assertNotIn("Use the repository map", self.call(0)["prompt"])

    def test_reviewer_sources_are_capped_and_name_what_was_left_out(self):
        (self.repo / "big.py").write_text("x" * 500)
        text = plan.plan_sources(self.repo, {"milestones": [{"files": ["app.py", "big.py", "new.py"]}]}, limit=100)
        self.assertIn("--- app.py", text)
        self.assertIn("Not included, read if needed: big.py", text)
        self.assertNotIn("new.py", text)

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
        self.assertIn("# Plan T-9", result.stdout)
        self.assertIn("friday approve T-9", result.stdout)
        result = subprocess.run([sys.executable, str(CLI), "approve", "T-9", "--no-run"], capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(card.read(self.root, "T-9")["state"], "approved")

    def test_decide_and_replan_commands_revise_a_waiting_plan(self):
        clean = {"verdict": "ok", "findings": []}
        self.calls(("p", PLAN), ("r", clean))
        self.start("T-7")
        environment = dict(os.environ, FRIDAY_HOME=str(self.root), FRIDAY_LANG="en")
        def friday(*argv):
            script = ("import sys, runpy; from unittest.mock import patch; "
                      "patch('friday.models.environment', return_value={'FAKE_DIR': %r}).start(); "
                      "sys.argv = ['friday', *%r]; runpy.run_path(%r, run_name='__main__')") % (str(self.fake), list(argv), str(CLI))
            return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=environment,
                                  cwd=str(Path(__file__).resolve().parents[1]))
        result = friday("decide", "T-7", "scope", "Drop the page milestone.")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("friday replan T-7", result.stdout)
        self.calls(("p2", dict(copy.deepcopy(PLAN), summary="Greeting function only.")), ("r2", clean))
        result = friday("replan", "T-7")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("# Plan T-7", result.stdout)
        self.assertIn("Greeting function only.", result.stdout)
        self.assertIn("friday approve T-7", result.stdout)
        self.assertEqual(card.read(self.root, "T-7")["state"], "awaiting_approval")
        result = friday("replan", "T-7")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no new decisions", result.stderr)

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
        self.assertIn("Plan approved automatically", result.stdout)
        self.assertEqual(card.read(self.root, "T-8")["state"], "approved")
        self.assertEqual(card.events(self.root, "T-8")[-1], dict(card.events(self.root, "T-8")[-1], type="plan_approved", by="auto"))


if __name__ == "__main__":
    unittest.main()
