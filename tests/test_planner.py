import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import autopilot, planner, state, workflow


class PlannerTests(unittest.TestCase):
    def setUp(self):
        for name in ("require_runner", "require_browser"):
            mock = patch("friday.isolation." + name)
            mock.start()
            self.addCleanup(mock.stop)

    def test_planner_resolves_questions_and_autopilot_continues(self):
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
            (repo / "source").write_text("code")
            git("add", "source")
            git("commit", "-qm", "initial")
            plan = {"stages": [{"id": "one", "brief": "implement", "criteria": ["works"], "files": ["source"],
                                "max_added_lines": 20}], "questions": ["Which value?"], "suggested_issues": []}
            script = root / "planner.py"
            capture = root / "request.json"
            environment = root / "environment.json"
            script.write_text("import json,os,sys\nr=json.load(sys.stdin)\nopen(" + repr(str(capture)) + ", 'w').write(json.dumps(r))\n"
                              "open(" + repr(str(environment)) + ", 'w').write(json.dumps([os.environ.get('FRIDAY_ZAI_TOKEN_FILE'), os.environ.get('FRIDAY_ZAI_TOKEN_KEY')]))\n"
                              "plan=" + repr(plan) + "\n"
                              "if 'previous_plan' in r: plan.update(questions=[], assumptions=['Use the existing value: it matches the project.'])\n"
                              "print(json.dumps(plan))\n")
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": str(repo), "engineering_policy": ["Use a documented precedent."],
                                          "agents": {"glm": {"vendor": "zai", "model": "example", "roles": ["review"], "token_file": "/private/secret"}},
                                          "skills": {"review": {"description": "Review changes", "path": "/private/skill"}},
                                          "runners": {"plan": {"model": "planner", "vendor": "zai", "token_file": "/synthetic/token",
                                                               "token_key": "SYNTHETIC_KEY", "command": ["python3", str(script)]}}}))
            state.create(root, "T-1", str(config), mode="auto")
            state.update(root, "T-1", lambda card: card.update({"worktree": str(repo), "branch": "friday/T-1"}))
            planner.generate(root, "T-1")
            self.assertNotIn("/private/secret", capture.read_text())
            self.assertNotIn("/private/skill", capture.read_text())
            self.assertEqual(json.loads(environment.read_text()), ["/synthetic/token", "SYNTHETIC_KEY"])
            self.assertEqual(workflow.unanswered(state.read(root, "T-1")), [])
            self.assertEqual(state.read(root, "T-1")["planning_assumptions"], ['Use the existing value: it matches the project.'])
            self.assertIn("previous_plan", json.loads(capture.read_text()))
            self.assertEqual(json.loads(capture.read_text())["engineering_policy"], ["Use a documented precedent."])
            state.update(root, "T-1", lambda card: card.update(planning_questions=["Legacy question?"]))
            def complete(root, key, stage):
                state.update(root, key, lambda card: card["stages"][stage].update(status="closed"))
                return {"stage": stage, "status": "closed"}
            with patch("friday.autopilot.run_stage", side_effect=complete):
                self.assertEqual(autopilot.run(root, "T-1")["remaining"], [])


if __name__ == "__main__":
    unittest.main()
