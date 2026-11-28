import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import models, card, plan, workspace
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
    json.dump({"argv": sys.argv[1:], "prompt": prompt, "agent": os.environ.get("FRIDAY_TEST_AGENT")}, handle)
call = json.load(open(os.path.join(directory, "calls.json")))[index]
print(json.dumps({"type": "system", "subtype": "init", "session_id": call["session"]}), flush=True)
error = call.get("error")
print(json.dumps({"type": "result", "subtype": "error_during_execution" if error else "success", "is_error": bool(error),
                  "session_id": call["session"], "result": error or "",
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
        (self.fake / "calls.json").write_text(json.dumps([
            {"session": item[0], "answer": item[1], "error": item[2] if len(item) > 2 else None} for item in answers]))
        (self.fake / "counter").unlink(missing_ok=True)

    def test_task_limits_bound_the_whole_plan(self):
        self.assertFalse(plan.validate(PLAN, self.repo))
        errors = plan.validate(PLAN, self.repo, {"task_milestones": 1, "task_stages": 1})
        self.assertTrue(any("2 milestones" in error and "limit is 1" in error for error in errors), errors)
        self.assertTrue(any("2 stages" in error and "limit is 1" in error for error in errors), errors)

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

    def test_ordered_file_creation_reaches_review_without_an_extra_model_run(self):
        document = copy.deepcopy(PLAN)
        document["milestones"][0]["stages"].append(dict(
            document["milestones"][0]["stages"][0], id="M1.2", files=["greet.py"], new_files=[]))
        self.calls(("planner", document), ("reviewer", {"verdict": "ok", "findings": []}))
        self.start()
        self.assertEqual(card.read(self.root, "T-1")["state"], "awaiting_approval")
        self.assertNotIn("plan_invalid", [event["type"] for event in card.events(self.root, "T-1")])
        self.assertEqual((self.fake / "counter").read_text(), "2")

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

    def test_planner_weighs_options_by_size_and_the_reviewer_looks_for_a_smaller_one(self):
        self.calls(("p1", PLAN), ("r1", {"verdict": "ok", "findings": []}))
        self.start("T-1")
        planner, reviewer = self.call(0)["prompt"], self.call(1)["prompt"]
        self.assertIn("record in assumptions every option you considered with its estimated files and added lines", planner)
        self.assertIn("unless a project rule you cite or a failure you can show rules it out", planner)
        self.assertIn("\"it is cleaner\" is not such a reason", planner)
        self.assertIn("is an assumption too: record it with the lines it costs", planner)
        self.assertIn("a simpler design with fewer new modules or lines that the plan did not consider", reviewer)
        self.assertIn("blocking when the plan's design is larger than the alternative by more than about a third", reviewer)
        self.assertIn("every option considered with its estimated files and added lines", plan.PROJECT_PLAN_RULES)

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
                         "origin/T-1")

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

    def project_profile(self):
        self.profile.update(toolset={"plan": "project"}, project={
            "plan_command": "/plan-stages {statement}", "plan_file": "ACTUAL_PLAN.md", "session_env": ["FRIDAY_TEST_AGENT"],
            "disallowed_tools": ["mcp__chrome-devtools"], "timeouts": {"total": 7200}})
        self.profile_path.write_text(json.dumps({k: v for k, v in self.profile.items() if k != "path"}))
        environment = patch.dict(os.environ, {"FRIDAY_TEST_AGENT": "agent.sock"})
        environment.start()
        self.addCleanup(environment.stop)

    def summary(self, questions=()):
        value = {k: v for k, v in copy.deepcopy(PLAN).items() if k != "responses"}
        value["questions"] = list(questions)
        return value

    def test_project_plan_is_the_project_command_in_a_project_session_without_friday_review(self):
        self.project_profile()
        self.calls(("ps-1", self.summary()))
        self.start()
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["plan_session"]), ("awaiting_approval", "ps-1"))
        self.assertEqual([item["id"] for item in value["milestones"]], ["M1", "M2"])
        self.assertEqual(value["plan"]["file"], str(Path(value["worktree"]) / "ACTUAL_PLAN.md"))
        self.assertEqual((self.fake / "counter").read_text(), "1")  # no Friday plan review
        call = self.call(0)
        self.assertTrue(call["prompt"].startswith("/plan-stages Сделать приветствие для пользователя."))
        self.assertIn("write the plan to ACTUAL_PLAN.md", call["prompt"])
        self.assertEqual(call["argv"][call["argv"].index("--permission-mode") + 1], "auto")
        self.assertEqual(call["argv"][call["argv"].index("--disallowedTools") + 1], "mcp__chrome-devtools")
        self.assertNotIn("--tools", call["argv"])
        self.assertNotIn("responses", json.loads(call["argv"][call["argv"].index("--json-schema") + 1])["required"])
        self.assertEqual(call["agent"], "agent.sock")
        self.assertIn("Project plan file: ", plan.render(self.root, "T-1"))

    def test_project_plan_session_continues_with_the_errors_of_an_infeasible_summary(self):
        self.project_profile()
        broken = self.summary()
        broken["milestones"][0]["stages"][0]["criteria"].append({"text": "Видно в браузере", "check": "behavior"})
        self.calls(("ps-1", broken), ("ps-1", self.summary()))
        self.start()
        retry = self.call(1)
        self.assertEqual(retry["argv"][retry["argv"].index("--resume") + 1], "ps-1")
        self.assertIn("not marked ui", retry["prompt"])
        self.assertEqual(card.read(self.root, "T-1")["state"], "awaiting_approval")

    def test_project_plan_session_cut_off_by_sleep_continues_once_by_itself(self):
        self.project_profile()
        self.calls(("ps-1", None, "API Error: Your computer went to sleep mid-response."), ("ps-1", self.summary()))
        self.start()
        retry = self.call(1)
        self.assertEqual(retry["argv"][retry["argv"].index("--resume") + 1], "ps-1")
        self.assertIn("do not start over", retry["prompt"])
        self.assertIn("went to sleep", retry["prompt"])
        self.assertEqual(card.read(self.root, "T-1")["state"], "awaiting_approval")
        self.assertIn("session_interrupted", [event["type"] for event in card.events(self.root, "T-1")])

    def test_project_plan_session_without_a_structured_answer_is_asked_for_it_once(self):
        self.project_profile()
        self.calls(("ps-1", None), ("ps-1", self.summary()))
        self.start()
        self.assertIn("without the structured answer", self.call(1)["prompt"])
        self.assertEqual(card.read(self.root, "T-1")["state"], "awaiting_approval")

    def test_failed_project_planning_keeps_its_session_and_resume_continues_it(self):
        self.project_profile()
        sleep = "API Error: Your computer went to sleep mid-response."
        self.calls(("ps-1", None, sleep), ("ps-1", None, sleep), ("ps-1", self.summary()))
        with self.assertRaises(FridayError):
            self.start()
        card.update(self.root, "T-1", lambda value: value.update(state="failed"))
        self.assertEqual(card.read(self.root, "T-1")["plan_session"], "ps-1")
        plan.resume_planning(self.root, "T-1", self.profile)
        resumed = self.call(2)
        self.assertEqual(resumed["argv"][resumed["argv"].index("--resume") + 1], "ps-1")
        self.assertEqual(card.read(self.root, "T-1")["state"], "awaiting_approval")

    def test_resume_of_failed_planning_without_a_session_asks_to_start_again(self):
        self.project_profile()
        card.create(self.root, "T-1", str(self.profile_path))
        card.update(self.root, "T-1", lambda value: value.update(state="failed"))
        with self.assertRaises(FridayError) as caught:
            plan.resume_planning(self.root, "T-1", self.profile)
        self.assertIn("friday start", str(caught.exception))

    def test_owner_answer_to_a_project_plan_continues_its_session_through_decide(self):
        self.project_profile()
        self.calls(("ps-1", self.summary(["Маску ИНН расширять?"])), ("ps-1", self.summary()))
        self.start()
        self.assertEqual(card.read(self.root, "T-1")["plan"]["questions"], ["Маску ИНН расширять?"])
        script = ("import sys, runpy; from unittest.mock import patch; "
                  "patch('friday.models.environment', return_value={'FAKE_DIR': %r}).start(); "
                  "sys.argv = ['friday', 'decide', 'T-1', 'mask', 'Yes, up to 12 digits']; "
                  "runpy.run_path(%r, run_name='__main__')") % (str(self.fake), str(CLI))
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                env=dict(os.environ, FRIDAY_HOME=str(self.root)), cwd=str(Path(__file__).resolve().parents[1]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("friday approve T-1", result.stdout)
        answer = self.call(1)
        self.assertEqual(answer["argv"][answer["argv"].index("--resume") + 1], "ps-1")
        self.assertIn("Yes, up to 12 digits", answer["prompt"])
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["plan"]["revision"], value["plan"]["questions"]), ("awaiting_approval", 2, []))
        self.assertEqual(value["decisions"][0]["name"], "mask")

    def cli(self, *argv):
        script = ("import sys, runpy; from unittest.mock import patch; "
                  "patch('friday.models.environment', return_value={'FAKE_DIR': %r}).start(); "
                  "sys.argv = ['friday'] + %r; runpy.run_path(%r, run_name='__main__')") % (str(self.fake), list(argv), str(CLI))
        return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              env=dict(os.environ, FRIDAY_HOME=str(self.root)), cwd=str(Path(__file__).resolve().parents[1]))

    def decided(self):
        """The planner's answer to a decision about M2; it also touches M1, which the decision does not name."""
        value = copy.deepcopy(PLAN)
        value["milestones"][0]["goal"] = "Заодно переписать модель."
        value["milestones"][1]["stages"][0].update(brief="Вывести в шапке", steps=["вставить в header"])
        return value

    def test_decision_before_approval_revises_the_named_milestones_and_reviews_only_them(self):
        clean = {"verdict": "ok", "findings": []}
        self.calls(("p", PLAN), ("r", clean))
        self.start()
        finding = {"milestone": "M2", "stage": "M2.1", "severity": "minor", "problem": "Шапки на странице нет.", "suggestion": "Создать."}
        answered = dict(self.decided(), responses=[{"finding": 0, "accepted": False, "reason": "Шапка есть в page.html."}])
        self.calls(("p2", self.decided()), ("r2", {"verdict": "ok", "findings": [finding]}), ("p3", answered))
        result = self.cli("decide", "T-1", "header", "Greeting in the header.", "--reopen", "M2")
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in ("Decision recorded.", "The plan is revised (milestones: M2).", "Only added to the context of milestones (M1)",
                         "friday plan T-1 --diff", "friday approve T-1", "Вывести в шапке"):
            self.assertIn(expected, result.stdout)
        self.assertNotIn("Reopened", result.stdout)
        revision, review = self.call(0), self.call(1)
        self.assertNotIn("--resume", revision["argv"])
        self.assertIn("Greeting in the header.", revision["prompt"])
        self.assertIn("Change only milestones M2;", revision["prompt"])
        self.assertIn("Your plan:", revision["prompt"])
        self.assertIn("changed milestones M2: review only them", review["prompt"])
        self.assertIn("--- page.html", review["prompt"])
        self.assertNotIn("--- app.py", review["prompt"])  # M1 is context: its files are not sent again
        self.assertNotIn("Заодно переписать модель.", review["prompt"])
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["plan"]["revision"]), ("awaiting_approval", 2))
        self.assertEqual([note["answer"] for note in value["plan"]["review_notes"]], ["Шапка есть в page.html."])
        document = json.loads((card.directory(self.root, "T-1") / "plan.json").read_text())
        self.assertEqual(document["milestones"][0], PLAN["milestones"][0])  # carried over as it was
        self.assertEqual(document["milestones"][1]["stages"][0]["steps"], ["вставить в header"])
        text = self.cli("plan", "T-1", "--diff").stdout
        self.assertIn("Changed milestones: M2", text)
        self.assertIn("-- **M2.1** Вывести на страницу (~20 lines)", text)
        self.assertIn("+  step: вставить в header", text)
        self.assertNotIn("Функция greet", text)

    def test_decision_without_reopen_lets_the_planner_choose_and_an_unchanged_plan_is_not_reviewed(self):
        self.calls(("p", PLAN), ("r", {"verdict": "ok", "findings": []}))
        self.start()
        self.assertIn("no previous revision", self.cli("plan", "T-1", "--diff").stderr)
        self.calls(("p2", PLAN))
        result = self.cli("decide", "T-1", "tone", "Use the formal address.")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("The planner changed no milestone.", result.stdout)
        self.assertIn("Only added to the context of milestones (M1, M2)", result.stdout)
        self.assertNotIn("--diff", result.stdout)
        self.assertIn("Change only the milestones the decision affects", self.call(0)["prompt"])
        self.assertEqual((self.fake / "counter").read_text(), "1")
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["plan"]["revision"]), ("awaiting_approval", 1))
        changed = copy.deepcopy(PLAN)
        changed["milestones"][0]["stages"][0]["steps"] = ["обращаться на «вы»"]
        self.calls(("p3", changed), ("r3", {"verdict": "ok", "findings": []}))
        self.assertEqual(plan.amend(self.root, "T-1", self.profile, "Use the formal address."), (["M1"], []))
        self.assertIn("changed milestones M1:", self.call(1)["prompt"])

    def test_failed_revision_by_a_decision_leaves_the_plan_waiting_and_the_decision_recorded_once(self):
        self.calls(("p", PLAN), ("r", {"verdict": "ok", "findings": []}))
        self.start()
        dropped = dict(copy.deepcopy(PLAN), milestones=[self.decided()["milestones"][1]])
        dropped["milestones"][0]["depends_on"] = []
        for _ in range(2):
            self.calls(("p2", dropped), ("p3", dropped))  # M1 is not named, yet the planner drops it twice
            result = self.cli("decide", "T-1", "header", "Greeting in the header.", "--reopen", "M2")
            self.assertEqual(result.returncode, 1)
            self.assertIn("M1 is outside the owner's decision", result.stderr)
        self.assertIn("M1 is outside the owner's decision", self.call(1)["prompt"])
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["plan"]["revision"], len(value["decisions"])), ("awaiting_approval", 1, 1))
        self.assertEqual(json.loads((card.directory(self.root, "T-1") / "plan.json").read_text())["milestones"], PLAN["milestones"])
        plan.approve(self.root, "T-1")
        with self.assertRaises(FridayError):
            plan.amend(self.root, "T-1", self.profile, "Поздно.")

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
