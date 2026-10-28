import contextlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import stages, state, workflow
from friday.errors import FridayError


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        preview_mock = patch('friday.isolated_preview.open_preview', side_effect=lambda *_: contextlib.nullcontext('http://test.localhost:1234/'))
        preview_mock.start()
        self.addCleanup(preview_mock.stop)
        for name in ("require_runner", "require_browser"):
            mock = patch("friday.isolation." + name)
            mock.start()
            self.addCleanup(mock.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        def git(*args):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        git("init", "-q")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        (self.repo / "file").write_text("code")
        git("add", "file")
        git("commit", "-qm", "initial")
        echo = ["python3", "-c", "import json,sys; print(json.dumps({'verdict': 'pass', 'evidence': ['observed'], 'seen': json.load(sys.stdin)}))"]
        runners = {"develop": {"model": "a", "command": echo},
                   "blind-behavior": {"model": "b", "command": echo}}
        self.config = self.root / "profile.json"
        self.config.write_text(json.dumps({"name": "test", "repository": str(self.repo), "runners": runners}))
        state.create(self.root, "T-1", str(self.config))
        self.plan = self.root / "plan.json"

    def write_plan(self, items):
        self.plan.write_text(json.dumps({"stages": items}))

    def test_dependencies_and_blind_behavior_brief(self):
        self.write_plan([{"id": "one", "brief": "secret implementation", "criteria": ["works"]},
                         {"id": "two", "brief": "second", "criteria": ["works"], "depends_on": ["one"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        self.assertEqual(workflow.ready(state.read(self.root, "T-1")), ["one"])
        result = workflow.invoke(self.root, "T-1", "one", "blind-behavior")
        request = json.loads(Path(result["evidence"]).read_text())["seen"]
        self.assertEqual(request, {"role": "blind-behavior", "criteria": ["works"], "model": "b", "preview_url": "http://test.localhost:1234/"})
        stages.close(self.root, "T-1", "one", "owner", "accepted")
        self.assertEqual(workflow.ready(state.read(self.root, "T-1")), ["two"])

    def test_blind_code_uses_isolated_workspace_and_zai_settings(self):
        config = json.loads(self.config.read_text())
        capture = ("import json,os,sys; request=json.load(sys.stdin); "
                   "print(json.dumps({'verdict':'pass','findings':[],'evidence':['checked'], "
                   "'request':request,'cwd':os.getcwd(),'token_file':os.environ.get('FRIDAY_ZAI_TOKEN_FILE'), "
                   "'token_key':os.environ.get('FRIDAY_ZAI_TOKEN_KEY')}))")
        config["runners"]["blind-code"] = {"model": "glm", "vendor": "zai", "command": ["python3", "-c", capture],
                                             "token_file": "/synthetic/token", "token_key": "SYNTHETIC_KEY"}
        self.config.write_text(json.dumps(config))
        self.write_plan([{"id": "one", "brief": "private", "criteria": ["works"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        result = workflow.invoke(self.root, "T-1", "one", "blind-code")
        output = json.loads(Path(result["evidence"]).read_text())
        self.assertEqual(output["request"]["vendor"], "zai")
        self.assertNotIn("worktree", output["request"])
        self.assertNotEqual(Path(output["cwd"]).resolve(), self.repo.resolve())
        self.assertEqual((output["token_file"], output["token_key"]), ("/synthetic/token", "SYNTHETIC_KEY"))

    def test_review_of_feedback_excludes_previously_published_changes(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()
        plan_base = git("rev-parse", "HEAD")
        (self.repo / "file").write_text("code\nprevious feature\n")
        git("add", "file"); git("commit", "-qm", "feature")
        published = git("rev-parse", "HEAD")
        (self.repo / "file").write_text("code\nprevious feature\nreview fix\n")
        git("add", "file"); git("commit", "-qm", "review fix")
        diff = workflow.review_diff(str(self.repo), {"plan_base": plan_base}, {"review_base": published})
        self.assertIn("+review fix", diff)
        self.assertNotIn("+previous feature", diff)

    def test_cycles_are_rejected_without_changing_card(self):
        self.write_plan([{"id": "one", "brief": "x", "criteria": ["a"], "depends_on": ["two"]},
                         {"id": "two", "brief": "y", "criteria": ["b"], "depends_on": ["one"]}])
        with self.assertRaises(FridayError) as error:
            workflow.load_plan(self.root, "T-1", self.plan)
        self.assertEqual(error.exception.code, "invalid_plan")
        self.assertEqual(state.read(self.root, "T-1")["stages"], {})

    def test_agent_team_is_validated_and_saved_with_the_plan(self):
        config = json.loads(self.config.read_text())
        config["agents"] = {"one": {"vendor": "openai", "model": "example", "roles": ["research"]}}
        self.config.write_text(json.dumps(config))
        assignment = {"id": "research", "agent": "one", "role": "research", "brief": "Inspect code", "skills": [], "depends_on": []}
        self.write_plan([{"id": "one", "brief": "x", "criteria": ["a"], "crew": [assignment]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        self.assertEqual(state.read(self.root, "T-1")["stages"]["one"]["crew"], [assignment])

    def test_behavior_brief_includes_test_data_policy(self):
        self.write_plan([{"id": "one", "brief": "implementation", "criteria": ["works"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        config = json.loads(self.config.read_text())
        config["behavior_test_policy"] = "Only staging; use ordinary application data."
        self.config.write_text(json.dumps(config))
        result = workflow.invoke(self.root, "T-1", "one", "blind-behavior")
        request = json.loads(Path(result["evidence"]).read_text())["seen"]
        self.assertEqual(request["behavior_test_policy"], config["behavior_test_policy"])

    def test_project_policy_reaches_developer_but_not_blind_browser(self):
        config = json.loads(self.config.read_text())
        config["engineering_policy"] = ["Use existing library capabilities."]
        self.config.write_text(json.dumps(config))
        self.write_plan([{"id": "one", "brief": "implementation", "criteria": ["works"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        workflow.approve_plan(self.root, "T-1")
        event = workflow.invoke(self.root, "T-1", "one", "develop")
        request = json.loads(Path(event["evidence"]).read_text())["seen"]
        self.assertEqual(request["engineering_policy"], config["engineering_policy"])
        event = workflow.invoke(self.root, "T-1", "one", "blind-behavior")
        self.assertNotIn("engineering_policy", json.loads(Path(event["evidence"]).read_text())["seen"])

    def test_changed_project_policy_invalidates_running_developer_result(self):
        config = json.loads(self.config.read_text())
        config["engineering_policy"] = ["Before."]
        script = self.root / "runner.py"
        script.write_text("import json,pathlib,sys\njson.load(sys.stdin)\np=pathlib.Path(" + repr(str(self.config)) + ")\n"
                          "v=json.loads(p.read_text()); v['engineering_policy']=['After.']; p.write_text(json.dumps(v))\nprint('done')\n")
        config["runners"]["develop"]["command"] = ["python3", str(script)]
        self.config.write_text(json.dumps(config))
        self.write_plan([{"id": "one", "brief": "implementation", "criteria": ["works"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        workflow.approve_plan(self.root, "T-1")
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, "T-1", "one", "develop")
        self.assertEqual(error.exception.code, "stale_evidence")

    def test_changed_project_policy_invalidates_existing_plan_before_development(self):
        config = json.loads(self.config.read_text())
        config["engineering_policy"] = ["Before."]
        self.config.write_text(json.dumps(config))
        self.write_plan([{"id": "one", "brief": "implementation", "criteria": ["works"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        workflow.approve_plan(self.root, "T-1")
        config["engineering_policy"] = ["After."]
        self.config.write_text(json.dumps(config))
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, "T-1", "one", "develop")
        self.assertEqual(error.exception.code, "stale_evidence")

    def test_same_model_cannot_review(self):
        self.write_plan([{"id": "one", "brief": "x", "criteria": ["a"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        config = json.loads(self.config.read_text())
        config["runners"]["blind-behavior"]["model"] = "a"
        self.config.write_text(json.dumps(config))
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, "T-1", "one", "blind-behavior")
        self.assertEqual(error.exception.code, "reviewer_not_independent")

    def test_review_cannot_pass_with_findings(self):
        self.write_plan([{"id": "one", "brief": "x", "criteria": ["a"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        config = json.loads(self.config.read_text())
        config["runners"]["blind-behavior"]["command"] = ["python3", "-c", "print('{\"verdict\":\"pass\",\"findings\":[\"problem\"]}')"]
        self.config.write_text(json.dumps(config))
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, "T-1", "one", "blind-behavior")
        self.assertEqual(error.exception.code, "invalid_verdict")

    def test_behavior_review_cannot_pass_without_observation(self):
        self.write_plan([{"id": "one", "brief": "x", "criteria": ["a"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        config = json.loads(self.config.read_text())
        config["runners"]["blind-behavior"]["command"] = ["python3", "-c", "print('{\"verdict\":\"pass\"}')"]
        self.config.write_text(json.dumps(config))
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, "T-1", "one", "blind-behavior")
        self.assertEqual(error.exception.code, "invalid_verdict")

    def test_manual_plan_needs_approval_and_replacement_invalidates_it(self):
        self.write_plan([{"id": "one", "brief": "x", "criteria": ["a"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, "T-1", "one", "develop")
        self.assertEqual(error.exception.code, "plan_approval_required")
        workflow.approve_plan(self.root, "T-1")
        self.assertTrue(state.read(self.root, "T-1")["plan_approved"])
        workflow.load_plan(self.root, "T-1", self.plan, replace=True)
        card = state.read(self.root, "T-1")
        self.assertFalse(card["plan_approved"])
        self.assertEqual(card["plan_revision"], 2)
        self.assertTrue(any(event["type"] == "plan_replaced" for event in card["events"]))


if __name__ == "__main__":
    unittest.main()
