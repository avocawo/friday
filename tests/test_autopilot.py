import contextlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import autopilot, state, workflow, worktree
from friday.errors import FridayError


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        stand = patch("friday.isolated_preview.open_preview", side_effect=lambda *_: contextlib.nullcontext("http://test.localhost:1234/"))
        stand.start()
        self.addCleanup(stand.stop)
        for name in ("require_runner", "require_browser"):
            mock = patch("friday.isolation." + name)
            mock.start()
            self.addCleanup(mock.stop)

    @unittest.skipUnless(os.environ.get("FRIDAY_DOCKER_TEST") == "1", "requires local alpine:3.20 image")
    def test_runs_development_reviews_and_gate_in_owned_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            git("branch", "-M", "friday/T-1")
            (repo / "source").write_text("old\n")
            git("add", "source")
            git("commit", "-qm", "initial")
            fake = root / "runner.py"
            fake.write_text("import json, pathlib, sys\nreq=json.load(sys.stdin)\n"
                            "if req['role']=='develop': pathlib.Path('source').write_text('new\\n')\n"
                            "print(json.dumps({'verdict':'pass','evidence':['observed']}))\n")
            runners = {role: {"model": "dev" if role == "develop" else "review", "command": ["python3", str(fake)]}
                       for role in ("develop", "review", "blind-code", "blind-behavior")}
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": str(repo), "runners": runners,
                                          "gate_image": "alpine:3.20", "gate": ["sh", "-c", "test \"$(cat source)\" = new"]}))
            state.create(root, "T-1", str(config), mode="auto")
            state.update(root, "T-1", lambda card: card.update({"worktree": str(repo), "branch": "friday/T-1"}))
            plan = root / "plan.json"
            plan.write_text(json.dumps({"stages": [{"id": "one", "brief": "update source", "criteria": ["source is new"]}]}))
            workflow.load_plan(root, "T-1", plan)
            real_progress = autopilot.set_progress
            def interrupted(*args, **changes):
                if changes.get("phase") == "review":
                    raise OSError("simulated crash after commit")
                return real_progress(*args, **changes)
            with patch("friday.autopilot.set_progress", side_effect=interrupted):
                with self.assertRaises(OSError):
                    autopilot.run(root, "T-1")
            self.assertEqual(state.read(root, "T-1")["stages"]["one"]["automation"]["phase"], "commit")
            result = autopilot.run(root, "T-1")
            self.assertEqual(result["remaining"], [])
            self.assertEqual(state.read(root, "T-1")["stages"]["one"]["status"], "closed")
            self.assertEqual((repo / "source").read_text(), "new\n")
            self.assertEqual(git("status", "--porcelain"), "")

    @unittest.skipUnless(os.environ.get("FRIDAY_DOCKER_TEST") == "1", "requires local alpine:3.20 image")
    def test_failed_review_is_sent_back_for_a_new_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            git("branch", "-M", "friday/T-1")
            (repo / "source").write_text("old")
            git("add", "source")
            git("commit", "-qm", "initial")
            fake = root / "runner.py"
            fake.write_text("import json,pathlib,sys\nr=json.load(sys.stdin)\n"
                            "if r['role']=='develop': pathlib.Path('source').write_text('fixed' if 'feedback' in r else 'first')\n"
                            "failed=r['role']=='review' and pathlib.Path('source').read_text()=='first'\n"
                            "print(json.dumps({'verdict':'fail' if failed else 'pass','findings':['needs fix'] if failed else [],'evidence':['observed']}))\n")
            runners = {role: {"model": "dev" if role == "develop" else "review", "command": ["python3", str(fake)]}
                       for role in ("develop", "review", "blind-code", "blind-behavior")}
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": str(repo), "runners": runners,
                                          "gate_image": "alpine:3.20", "gate": ["sh", "-c", "test \"$(cat source)\" = fixed"]}))
            state.create(root, "T-1", str(config), mode="auto")
            state.update(root, "T-1", lambda card: card.update({"worktree": str(repo), "branch": "friday/T-1"}))
            plan = root / "plan.json"
            plan.write_text(json.dumps({"stages": [{"id": "one", "brief": "update", "criteria": ["works"]}]}))
            workflow.load_plan(root, "T-1", plan)
            result = autopilot.run(root, "T-1")
            self.assertEqual(result["remaining"], [])
            self.assertEqual((repo / "source").read_text(), "fixed")
            self.assertEqual(state.read(root, "T-1")["stages"]["one"]["automation"]["attempt"], 2)

    def test_resume_never_commits_a_changed_developer_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            git("branch", "-M", "friday/T-1")
            (repo / "source").write_text("old")
            git("add", "source")
            git("commit", "-qm", "initial")
            head = git("rev-parse", "HEAD")
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": str(repo)}))
            state.create(root, "T-1", str(config), mode="auto")
            state.update(root, "T-1", lambda card: card.update({"worktree": str(repo), "branch": "friday/T-1"}))
            plan = root / "plan.json"
            plan.write_text(json.dumps({"stages": [{"id": "one", "brief": "update", "criteria": ["works"]}]}))
            workflow.load_plan(root, "T-1", plan)
            (repo / "source").write_text("developer result")
            autopilot.set_progress(root, "T-1", "one", phase="commit", attempt=1, head=head,
                                   snapshot=worktree.fingerprint(str(repo)))
            (repo / "source").write_text("other user content")
            with self.assertRaises(FridayError) as error:
                autopilot.run(root, "T-1")
            self.assertEqual(error.exception.code, "stale_evidence")
            self.assertEqual(git("rev-parse", "HEAD"), head)
            self.assertEqual((repo / "source").read_text(), "other user content")

    def test_replacement_checkout_is_not_treated_as_owned_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repos = [root / "project", root / "replacement"]
            for repo in repos:
                repo.mkdir()
                def git(*args):
                    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
                git("init", "-q")
                git("config", "user.name", "Test")
                git("config", "user.email", "test@example.invalid")
                git("branch", "-M", "friday/T-1")
                (repo / "source").write_text("code")
                git("add", "source")
                git("commit", "-qm", "initial")
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": str(repos[0])}))
            state.create(root, "T-1", str(config), mode="auto")
            state.update(root, "T-1", lambda card: card.update({"worktree": str(repos[1]), "branch": "friday/T-1",
                                                                "plan_approved": True,
                                                                "stages": {"one": {"status": "open", "brief": "x", "criteria": ["x"]}}}))
            with self.assertRaises(FridayError) as error:
                autopilot.run(root, "T-1")
            self.assertEqual(error.exception.code, "worktree_conflict")

    def test_go_stops_before_planning_when_stage_session_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": str(root), "browser": {"auth": {}}}))
            state.create(root, "T-1", str(config), mode="auto")
            with patch("friday.autopilot.browser_auth.capture", side_effect=FridayError("stage_auth_unavailable", "missing")), \
                 patch("friday.autopilot.planner.generate") as planner:
                with self.assertRaises(FridayError) as error:
                    autopilot.run(root, "T-1")
            self.assertEqual(error.exception.code, "stage_auth_unavailable")
            planner.assert_not_called()

    def test_missing_browser_retries_review_without_rewriting_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); repo = root / "repo"; repo.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q"); git("config", "user.name", "Test"); git("config", "user.email", "test@example.invalid")
            git("branch", "-M", "friday/T-1"); (repo / "source").write_text("code")
            git("add", "source"); git("commit", "-qm", "initial"); head = git("rev-parse", "HEAD")
            config = root / "profile.json"; config.write_text(json.dumps({"name": "test", "repository": str(repo)}))
            state.create(root, "T-1", str(config), mode="auto")
            state.update(root, "T-1", lambda c: c.update(worktree=str(repo), branch="friday/T-1", plan_approved=True,
                stages={"one": {"status": "open", "criteria": ["works"], "depends_on": [],
                                "automation": {"phase": "blind-behavior", "attempt": 1, "head": head}}}))
            evidence = root / "review.json"
            evidence.write_text(json.dumps({"verdict": "fail", "findings": ["ENVIRONMENT-UNAVAILABLE: browser missing"]}))
            with patch("friday.autopilot.check_preflight"), patch("friday.autopilot.workflow.invoke", return_value={"verdict": "fail", "evidence": str(evidence)}):
                with self.assertRaises(FridayError) as error:
                    autopilot.run_stage(root, "T-1", "one")
            self.assertEqual(error.exception.code, "environment_unavailable")
            self.assertEqual(state.read(root, "T-1")["stages"]["one"]["automation"]["phase"], "blind-behavior")
            self.assertEqual(git("rev-parse", "HEAD"), head)


if __name__ == "__main__":
    unittest.main()
