import contextlib
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, final, milestone, models, runner, workspace
from friday.errors import FridayError
from test_milestone import DONE, MODELS, PASS, git

# Answers are chosen by what the prompt asks for, so the order of concurrent final checks does not matter.
FAKE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
directory = os.environ["FAKE_DIR"]
prompt = sys.stdin.read()
kinds = [("You do the final review", "final"), ("You check a change blind", "blind"), ("The final review of the whole task", "fix"),
         ("You review one milestone", "review"), ("Check the behavior criteria", "browser"), ("You implement", "develop"),
         ("The browser acceptance", "develop"), ("A reviewer of another vendor", "develop"), ("The isolated gate", "develop")]
kind = next((name for start, name in kinds if prompt.startswith(start)), None)
if kind is None:
    sys.exit("unexpected prompt: " + prompt[:200])
counter = os.path.join(directory, kind)
index = int(open(counter).read()) if os.path.exists(counter) else 0
open(counter, "w").write(str(index + 1))
call = json.load(open(os.path.join(directory, "calls.json")))[kind][index]
with open(os.path.join(directory, "log.jsonl"), "a") as handle:
    handle.write(json.dumps({"kind": kind, "argv": sys.argv[1:], "cwd": os.getcwd(), "files": os.listdir("."), "prompt": prompt}) + "\n")
for path, content in call.get("write", {}).items():
    open(path, "w").write(content)
for message in call.get("commit", []):
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", message], check=True)
print(json.dumps({"type": "system", "subtype": "init", "session_id": kind}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": kind, "result": "",
                  "structured_output": call["answer"], "total_cost_usd": 0.01, "num_turns": 1, "usage": {}}), flush=True)
'''

PLAN = {"summary": "Кнопка приветствия. Показывает имя.", "rules": ["Тесты рядом (AGENTS.md)"], "assumptions": [], "questions": [],
        "responses": [], "milestones": [
            {"id": "M1", "title": "Кнопка", "goal": "кнопка", "complexity": "clear", "ui": True, "parallel": False, "depends_on": [],
             "files": ["page.html"], "stages": [{"id": "M1.1", "brief": "кнопка", "steps": [], "files": ["page.html"], "new_files": [],
                                                 "estimate_lines": 5, "criteria": [{"text": "в коде есть кнопка", "check": "code"},
                                                                                   {"text": "по клику видно «Привет»", "check": "behavior"}]}]}]}
BLOCK = {"verdict": "fail", "criteria_results": [], "findings": [
    {"severity": "blocking", "file": "page.html", "problem": "Нет aria-label.", "evidence": "page.html:1"},
    {"severity": "blocking", "file": "page.html", "problem": "Нужен отдельный компонент.", "evidence": "page.html:1"},
    {"severity": "minor", "file": "page.html", "problem": "Отступы.", "evidence": "page.html:1"}]}


class FinalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root, self.fake = base / "home", base / "fake"
        (self.fake / "bin").mkdir(parents=True)
        (self.fake / "bin" / "claude").write_text(FAKE)
        (self.fake / "bin" / "claude").chmod(0o755)
        self.origin, repo = base / "origin.git", base / "repo"
        git("init", "-q", "--bare", "-b", "main", str(self.origin))
        git("clone", "-q", str(self.origin), str(repo))
        (repo / "page.html").write_text("<p></p>\n")
        git("add", ".", cwd=repo)
        git("commit", "-qm", "init", cwd=repo)
        git("push", "-q", "origin", "HEAD:main", cwd=repo)
        git("config", "user.name", "Owner", cwd=repo)
        git("config", "user.email", "owner@example.com", cwd=repo)
        self.profile_path = base / "profile.json"
        self.profile = {"name": "demo", "repository": str(repo), "base_branch": "main", "worktree_root": str(base / "wt"),
                        "models": MODELS, "roles": {"plan": ["big"], "plan-review": ["rival"], "develop": ["worker"], "review": ["big"]},
                        "browser": {"mode": "local"}, "preview": {"backend": "docker-local"}}
        self.profile_path.write_text(json.dumps(self.profile))
        self.profile["path"] = str(self.profile_path)
        for patcher in (patch.dict(os.environ, {"PATH": str(self.fake / "bin") + os.pathsep + os.environ.get("PATH", ""),
                                                "FAKE_DIR": str(self.fake)}),
                        patch("friday.models.environment", return_value={"FAKE_DIR": str(self.fake)})):
            patcher.start()
            self.addCleanup(patcher.stop)
        card.create(self.root, "T-1", str(self.profile_path))
        self.worktree = workspace.create(self.root, "T-1", self.profile)
        (card.directory(self.root, "T-1") / "plan.json").write_text(json.dumps(PLAN))
        layout = {role: dict(MODELS[name], name=name) for role, name in
                  {"plan": "big", "plan-review": "rival", "develop": "worker", "review": "big", "final-review": "big",
                   "blind-code": "rival", "browser": "big"}.items()}
        skeleton = [{"id": "M1", "title": "Кнопка", "complexity": "clear", "ui": True, "parallel": False, "depends_on": [],
                     "state": "pending", "stages": []}]
        card.update(self.root, "T-1", lambda value: value.update(state="approved", cast=layout, milestones=skeleton,
                                                                 plan=dict(value["plan"], approved=True)))

    def answers(self, **kinds):
        (self.fake / "calls.json").write_text(json.dumps(kinds))

    def log(self, kind=None):
        source = self.fake / "log.jsonl"
        entries = [json.loads(line) for line in source.read_text().splitlines()] if source.exists() else []
        return [entry for entry in entries if kind is None or entry["kind"] == kind]

    def commit_work(self):
        (Path(self.worktree) / "page.html").write_text("<button>hi</button>\n")
        git("commit", "-qam", "T-1 M1.1: button", cwd=self.worktree)
        card.update(self.root, "T-1", lambda value: value["milestones"][0].update(state="done", stages=[{"id": "M1.1", "commit": "abc"}]))

    def test_clean_final_checks_save_the_merge_request_text(self):
        self.commit_work()
        self.answers(final=[{"answer": PASS}], blind=[{"answer": dict(PASS, findings=[
            {"severity": "minor", "file": "page.html", "problem": "Отступы.", "evidence": "page.html:1"}])}])
        result = milestone.finish(self.root, "T-1", self.profile)
        self.assertEqual(result["transport"], "none")
        text = Path(result["description"]).read_text()
        for expected in ("Кнопка приветствия.", "**M1. Кнопка** — готово", "## Мелкие замечания", "Отступы.", "Friday: $"):
            self.assertIn(expected, text)
        self.assertNotIn("На решение владельцу", text)
        blind = self.log("blind")[0]
        self.assertEqual(blind["files"], [])  # the blind check runs in an empty directory
        self.assertNotIn("Тесты рядом", blind["prompt"])
        self.assertNotIn("по клику видно", blind["prompt"])  # behavior criteria are not for the blind code check
        self.assertIn("+<button>hi</button>", blind["prompt"])
        models = {entry["kind"]: entry["argv"][entry["argv"].index("--model") + 1] for entry in self.log()}
        self.assertEqual((models["final"], models["blind"]), ("big-1", "rival-1"))

    def test_resume_keeps_a_completed_peer_when_another_review_fails(self):
        self.commit_work()
        for failure in ("quota", "exception"):
            with self.subTest(failure=failure):
                card.update(self.root, "T-1", lambda value: value.update(final_reviews={}))
                calls = []
                def review(root, key, profile, role, *args):
                    calls.append(role)
                    if role == "blind-code" and calls.count(role) == 1:
                        if failure == "exception":
                            raise FridayError("agent_failed", "review interrupted")
                        return runner.Result("quota", error="quota reached")
                    return runner.Result("ok", structured=PASS)
                with self.assertRaises(FridayError):
                    final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA)
                self.assertIn("final-review", card.read(self.root, "T-1").get("final_reviews", {}))
                choose = models.reviewer
                def only_missing(profile, layout, role, *args):
                    self.assertEqual(role, "blind-code", "a completed reviewer need not have quota to reuse its result")
                    return choose(profile, layout, role, *args)
                with patch("friday.models.reviewer", side_effect=only_missing):
                    results, entry = final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA)
                self.assertEqual(set(results), {"final-review", "blind-code"})
                self.assertEqual(entry["model"], "big-1")
                self.assertEqual(calls.count("final-review"), 1)
                self.assertEqual(calls.count("blind-code"), 2)

    def test_review_reuse_requires_unchanged_source_and_review_context(self):
        self.commit_work()
        calls = []
        def review(root, key, profile, role, *args):
            calls.append(role)
            return runner.Result("ok", structured=PASS)
        def check():
            return final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA)
        check()
        check()
        self.assertEqual(len(calls), 2)
        (Path(self.worktree) / "page.html").write_text("<button>hello</button>\n")
        git("commit", "-qam", "changed source", cwd=self.worktree)
        check()
        self.assertEqual(len(calls), 4)
        plan_path = card.directory(self.root, "T-1") / "plan.json"
        document = json.loads(plan_path.read_text())
        document["milestones"][0]["stages"][0]["criteria"][0]["text"] += " updated"
        plan_path.write_text(json.dumps(document))
        check()
        self.assertEqual(len(calls), 6)
        card.update(self.root, "T-1", lambda value: value["decisions"].append(
            {"name": "clarification", "text": "Keep the current behavior.", "reopen": []}))
        check()
        self.assertEqual(len(calls), 8)
        self.profile["executor_deny_read"] = ["/unavailable-private-directory"]
        check()
        self.assertEqual(len(calls), 10)
        changed_schema = dict(milestone.REVIEW_SCHEMA, description="updated review schema")
        final.final_review(self.root, "T-1", self.profile, review, changed_schema)
        self.assertEqual(len(calls), 12)

    def test_owner_decisions_reach_each_independent_final_reviewer(self):
        self.commit_work()
        decision = 'Use the approved runtime choice instead of the original literal setting.'
        card.update(self.root, 'T-1', lambda value: value['decisions'].append(
            dict(name='runtime', text=decision, reopen=[])))
        prompts = {}
        def review(root, key, profile, role, entry, worktree, prompt, *args):
            prompts[role] = prompt
            return runner.Result('ok', structured=PASS)
        final.final_review(self.root, 'T-1', self.profile, review, milestone.REVIEW_SCHEMA)
        self.assertEqual(set(prompts), {'final-review', 'blind-code'})
        for role, prompt in prompts.items():
            with self.subTest(role=role):
                self.assertIn(decision, prompt)

    def test_dirty_tracked_or_untracked_files_reject_review_before_agent_calls(self):
        self.commit_work()
        calls = []
        def review(root, key, profile, role, *args):
            calls.append(role)
            return runner.Result("ok", structured=PASS)
        check = lambda: final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA)
        check()
        saved = card.read(self.root, "T-1").get("final_reviews")
        untracked = Path(self.worktree) / "untracked.txt"
        untracked.write_text("review context outside the diff\n")
        with self.assertRaises(FridayError) as error:
            check()
        self.assertEqual(error.exception.code, "stale_evidence")
        self.assertEqual(len(calls), 2)
        self.assertEqual(card.read(self.root, "T-1").get("final_reviews"), saved)
        untracked.unlink()
        check()
        self.assertEqual(len(calls), 2)
        (Path(self.worktree) / "page.html").write_text("uncommitted change\n")
        with self.assertRaises(FridayError) as error:
            check()
        self.assertEqual(error.exception.code, "stale_evidence")
        self.assertEqual(len(calls), 2)
        self.assertEqual(card.read(self.root, "T-1").get("final_reviews"), saved)

    def test_showcase_changes_invalidate_reuse_and_dirty_showcase_disables_it(self):
        self.commit_work()
        showcase = Path(self.directory.name) / "showcase"
        git("init", "-q", "-b", "main", str(showcase))
        story = showcase / "story.txt"
        story.write_text("original\n")
        git("add", ".", cwd=showcase)
        git("commit", "-qm", "initial story", cwd=showcase)
        base = git("rev-parse", "HEAD", cwd=showcase).strip()
        card.update(self.root, "T-1", lambda value: value.update(stories={"worktree": str(showcase), "base": base}))
        calls = []
        def review(root, key, profile, role, *args):
            calls.append(role)
            return runner.Result("ok", structured=PASS)
        check = lambda: final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA)
        check()
        check()
        self.assertEqual(len(calls), 2)
        story.write_text("updated\n")
        git("commit", "-qam", "changed story", cwd=showcase)
        check()
        self.assertEqual(len(calls), 4)
        story.write_text("uncommitted story\n")
        with self.assertRaises(FridayError) as error:
            check()
        self.assertEqual(error.exception.code, "stale_evidence")
        self.assertEqual(len(calls), 4)

    def test_changed_external_guidance_invalidates_only_its_review(self):
        self.commit_work()
        calls = []
        def review(root, key, profile, role, *args):
            calls.append(role)
            return runner.Result("ok", structured=PASS)
        with patch("friday.design.guidance", return_value="original review reference"):
            final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA)
        with patch("friday.design.guidance", return_value="updated review reference"):
            final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA)
        self.assertEqual(calls.count("final-review"), 2)
        self.assertEqual(calls.count("blind-code"), 1)

    def test_budget_increase_reuses_completed_peer_after_admission_failure(self):
        self.commit_work()
        calls = []
        def review(root, key, profile, role, *args):
            calls.append(role)
            if role == "blind-code" and profile["budget"]["max_model_runs"] == 1:
                raise FridayError("budget_exhausted", "The model-call budget is exhausted.")
            return runner.Result("ok", structured=PASS)
        self.profile["budget"] = {"max_model_runs": 1}
        self.profile["max_parallel_milestones"] = 1
        with self.assertRaises(FridayError):
            final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA)
        self.profile["budget"]["max_model_runs"] = 2
        self.profile["max_parallel_milestones"] = 2
        final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA)
        self.assertEqual(calls.count("final-review"), 1)
        self.assertEqual(calls.count("blind-code"), 2)

    def test_review_is_not_saved_if_its_context_changes_while_it_runs(self):
        self.commit_work()
        def review(*args):
            card.update(self.root, "T-1", lambda value: value["decisions"].append(
                {"name": "late clarification", "text": "new context", "reopen": []}))
            return runner.Result("ok", structured=PASS)
        with self.assertRaises(FridayError) as error:
            final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA, roles=("final-review",))
        self.assertEqual(error.exception.code, "stale_evidence")
        self.assertFalse(card.read(self.root, "T-1").get("final_reviews"))

    def test_source_drift_during_review_rejects_the_obsolete_verdict(self):
        self.commit_work()
        def review(*args):
            (Path(self.worktree) / "page.html").write_text("changed during review\n")
            return runner.Result("ok", structured=PASS)
        with self.assertRaises(FridayError) as error:
            final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA, roles=("final-review",))
        self.assertEqual(error.exception.code, "stale_evidence")
        self.assertFalse(card.read(self.root, "T-1").get("final_reviews"))

    def test_profile_can_request_one_final_review_without_selecting_a_blind_reviewer(self):
        self.commit_work()
        self.profile["final_review_roles"] = ["final-review"]
        self.answers(final=[{"answer": PASS}])
        choose = models.reviewer
        def only_requested(profile, layout, role, *args):
            self.assertEqual(role, "final-review")
            return choose(profile, layout, role, *args)
        with patch("friday.models.reviewer", side_effect=only_requested):
            milestone.finish(self.root, "T-1", self.profile)
        self.assertEqual(len(self.log("final")), 1)
        self.assertEqual(self.log("blind"), [])

    def test_explicit_final_roles_do_not_select_an_omitted_reviewer(self):
        self.commit_work()
        choose = models.reviewer
        def only_requested(profile, layout, role, *args):
            self.assertEqual(role, "final-review")
            return choose(profile, layout, role, *args)
        def review(*args):
            return runner.Result("ok", structured=PASS)
        with patch("friday.models.reviewer", side_effect=only_requested):
            results, _ = final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA,
                                            roles=("final-review",))
        self.assertEqual(set(results), {"final-review"})

    def test_available_review_is_saved_even_if_peer_selection_fails(self):
        self.commit_work()
        calls = []
        choose = models.reviewer
        def missing_final(profile, layout, role, *args):
            if role == "final-review":
                raise FridayError("cast_impossible", "No available final reviewer")
            return choose(profile, layout, role, *args)
        def review(root, key, profile, role, *args):
            calls.append(role)
            return runner.Result("ok", structured=PASS)
        with patch("friday.models.reviewer", side_effect=missing_final), self.assertRaises(FridayError):
            final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA)
        self.assertEqual(calls, ["blind-code"])
        final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA)
        self.assertEqual(calls, ["blind-code", "final-review"])

    def test_both_requested_reviews_still_run_concurrently(self):
        self.commit_work()
        barrier = threading.Barrier(2)
        def review(root, key, profile, role, entry, worktree, *args):
            barrier.wait(timeout=5)
            if role == "blind-code":
                self.assertNotEqual(worktree, self.worktree)
                self.assertEqual(list(Path(worktree).iterdir()), [])
            return runner.Result("ok", structured=PASS)
        results, _ = final.final_review(self.root, "T-1", self.profile, review, milestone.REVIEW_SCHEMA)
        self.assertEqual(set(results), {"final-review", "blind-code"})

    def test_blocking_findings_get_one_fix_round_and_disputes_go_to_the_owner(self):
        self.commit_work()
        fix = dict(DONE, disputes=[{"finding": 1, "reason": "Компоненты в проекте не выделяют.", "evidence": "AGENTS.md:2"}])
        self.answers(final=[{"answer": PASS}, {"answer": PASS}], blind=[{"answer": BLOCK}],
                     fix=[{"answer": fix, "write": {"page.html": "<button aria-label='hi'>hi</button>\n"}, "commit": ["T-1 final: aria"]}])
        milestone.finish(self.root, "T-1", self.profile)
        self.assertEqual(len(self.log("blind")), 1)  # the blind check is not repeated
        self.assertEqual(len(self.log("final")), 2)
        fix_call = self.log("fix")[0]
        self.assertIn("0. [blocking] page.html: Нет aria-label.", fix_call["prompt"])
        self.assertIn("--session-id", fix_call["argv"])
        value = card.read(self.root, "T-1")
        self.assertEqual([d["problem"] for d in value["disputes"]], ["Нужен отдельный компонент."])
        self.assertEqual([f["problem"] for f in value["final_minor"]], ["Отступы."])
        text = (card.directory(self.root, "T-1") / "merge-request.md").read_text()
        self.assertIn("## На решение владельцу", text)
        self.assertIn("Компоненты в проекте не выделяют.", text)
        self.assertIn("T-1 final: aria", git("log", "-1", "--format=%s", cwd=self.worktree))

    def test_draft_is_pushed_and_opened_once(self):
        self.commit_work()
        self.profile.update(delivery={"transport": "github", "target_branch": "main"}, github={"repository": "o/r"})
        self.answers(final=[{"answer": PASS}, {"answer": PASS}], blind=[{"answer": PASS}, {"answer": PASS}])
        created = []
        def create(profile, branch, target, title, body):
            created.append((branch, target, title, body))
            return {"iid": 7, "url": "https://example.test/o/r/pull/7"}
        with patch.dict("friday.vcs.TRANSPORTS", {"github": (lambda *args: [], create)}):
            result = milestone.finish(self.root, "T-1", self.profile)
            again = final.deliver(self.root, "T-1", self.profile)
        self.assertEqual(result["url"], "https://example.test/o/r/pull/7")
        self.assertEqual(again["url"], result["url"])
        self.assertEqual(len(created), 1)
        branch, target, title, body = created[0]
        self.assertEqual((branch, target, title), ("origin/T-1", "main", "T-1: Кнопка приветствия"))
        self.assertIn("**M1. Кнопка**", body)
        remote = git("ls-remote", str(self.origin), "refs/heads/origin/T-1").split()[0]
        self.assertEqual(remote, git("rev-parse", "HEAD", cwd=self.worktree).strip())

    def test_foreign_remote_branch_is_not_overwritten(self):
        self.commit_work()
        self.profile.update(delivery={"transport": "github", "target_branch": "main"})
        git("push", "-q", "origin", "main:refs/heads/origin/T-1", cwd=self.worktree)
        with self.assertRaises(FridayError) as error:
            final.deliver(self.root, "T-1", self.profile)
        self.assertEqual(error.exception.code, "remote_conflict")

    def test_ui_milestone_is_accepted_in_the_browser_and_failures_go_back(self):
        @contextlib.contextmanager
        def preview(profile, worktree):
            yield "http://t1.localhost:5201/"
        failing = {"verdict": "fail", "criteria_results": [{"criterion": "по клику видно «Привет»", "status": "fail", "evidence": "пусто"}],
                   "findings": [{"severity": "blocking", "file": "page.html", "problem": "По клику ничего не видно.", "evidence": "снимок"}]}
        unverifiable = {"verdict": "pass", "criteria_results": [{"criterion": "по клику видно «Привет»", "status": "unverifiable",
                                                                 "evidence": "401"}], "findings": []}
        self.answers(develop=[{"answer": DONE, "write": {"page.html": "<button>hi</button>\n"}, "commit": ["T-1 M1.1: button"]},
                              {"answer": DONE, "write": {"page.html": "<button onclick=x>hi</button>\n"}, "commit": ["T-1 M1.1: click"]}],
                     review=[{"answer": PASS}, {"answer": PASS}], browser=[{"answer": failing}, {"answer": unverifiable}])
        with patch("friday.isolated_preview.open_preview", side_effect=preview):
            milestone.run(self.root, "T-1", self.profile, "M1")
        browser = self.log("browser")[0]
        argv = browser["argv"]
        config = json.loads(argv[argv.index("--mcp-config") + 1])
        self.assertIn("browser_mcp.py", config["mcpServers"]["friday"]["args"][0])
        self.assertEqual(config["mcpServers"]["friday"]["args"][-1], "http://t1.localhost:5201/")
        self.assertIn("mcp__friday__take_snapshot", argv[argv.index("--allowedTools") + 1])
        self.assertNotIn("в коде есть кнопка", browser["prompt"])
        self.assertIn("browser acceptance check saw", self.log("develop")[1]["prompt"])
        self.assertIn("По клику ничего не видно.", self.log("develop")[1]["prompt"])
        types = [event["type"] for event in card.events(self.root, "T-1")]
        self.assertIn("browser_unverifiable", types)
        self.assertEqual(card.read(self.root, "T-1")["milestones"][0]["state"], "done")


if __name__ == "__main__":
    unittest.main()
