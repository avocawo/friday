import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from friday import card, profile
from friday.errors import FridayError
from tests import test_plan

ROOT = Path(__file__).resolve().parents[1]
# Fails once and leaves a mark, so the second run passes: the session gets one chance to fix what the check reports.
CHECK_ONCE = ["sh", "-c", "f={worktree}/.checked; [ -e $f ] && exit 0; touch $f; echo 'lint: {key} R-01 has no answer'; exit 1"]


class AnalysisTests(unittest.TestCase):
    setUp, calls, call = test_plan.PlanTests.setUp, test_plan.PlanTests.calls, test_plan.PlanTests.call

    def analysis_profile(self, check=None):
        self.profile.update(kind="analysis", toolset={"plan": "project"}, project=dict(
            {"analysis_command": "/accept {key}", "session_env": [], "disallowed_tools": []},
            **({"analysis_check": check} if check else {})))
        self.profile_path.write_text(json.dumps({k: v for k, v in self.profile.items() if k != "path"}))

    def cli(self, *argv, lang="en"):
        script = ("import sys, runpy; from unittest.mock import patch; "
                  "patch('friday.models.environment', return_value={'FAKE_DIR': %r}).start(); "
                  "sys.argv = ['friday'] + %r; runpy.run_path(%r, run_name='__main__')") % (str(self.fake), list(argv), str(test_plan.CLI))
        return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=str(ROOT),
                              env=dict(os.environ, FRIDAY_HOME=str(self.root), FRIDAY_LANG=lang))

    # The session's questions stop the task; decide resumes the same session, and a finished session closes it
    def test_questions_stop_the_task_and_the_answer_resumes_the_session_until_done(self):
        self.analysis_profile(check=["true"])
        self.calls(("as-1", {"summary": "Facts collected.", "questions": ["Use the kit's Drawer?"]}),
                   ("as-1", {"summary": "Documents written to the vault.", "questions": []}))
        started = self.cli("start", "T-1", "--profile", str(self.profile_path))
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertIn("Use the kit's Drawer?", started.stdout)
        self.assertIn("friday decide T-1", started.stdout)
        self.assertNotIn("friday approve", started.stdout)
        first = self.call(0)
        self.assertTrue(first["prompt"].startswith("/accept T-1\n"))
        self.assertIn("Never wait for an answer", first["prompt"])
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["analysis_session"], value["milestones"]), ("stopped", "as-1", []))

        answered = self.cli("decide", "T-1", "drawer", "Yes, the kit's Drawer")
        self.assertEqual(answered.returncode, 0, answered.stderr)
        resumed = self.call(1)
        self.assertEqual(resumed["argv"][resumed["argv"].index("--resume") + 1], "as-1")
        self.assertIn("Yes, the kit's Drawer", resumed["prompt"])
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value.get("questions")), ("done", None))
        self.assertEqual(value["analysis_summary"], "Documents written to the vault.")
        self.assertIn("Documents written to the vault.", answered.stdout)

    # A brief stands in for a missing ticket
    def test_brief_reaches_the_session(self):
        self.analysis_profile()
        self.calls(("as-1", {"summary": "Done.", "questions": []}))
        brief = Path(self.directory.name) / "brief.md"
        brief.write_text("Mockups: figma.com/design/x. No ticket yet.")
        self.assertEqual(self.cli("start", "T-1", "--profile", str(self.profile_path), "--brief", str(brief)).returncode, 0)
        self.assertIn("this brief from the owner stands in for it:\nMockups: figma.com/design/x. No ticket yet.", self.call(0)["prompt"])
        self.assertEqual(card.read(self.root, "T-1")["task_statement"], "Mockups: figma.com/design/x. No ticket yet.")

    # A failing check goes back to the session once with its output
    def test_failing_check_returns_its_output_to_the_session(self):
        self.analysis_profile(check=CHECK_ONCE)
        self.calls(("as-1", {"summary": "Done.", "questions": []}), ("as-1", {"summary": "Fixed.", "questions": []}))
        result = self.cli("start", "T-1", "--profile", str(self.profile_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        retry = self.call(1)
        self.assertEqual(retry["argv"][retry["argv"].index("--resume") + 1], "as-1")
        self.assertIn("lint: T-1 R-01 has no answer", retry["prompt"])
        self.assertEqual(card.read(self.root, "T-1")["state"], "done")

    # A check that still fails after the fix fails the task with its output
    def test_check_failing_twice_fails_the_task(self):
        self.analysis_profile(check=["sh", "-c", "echo 'lint: stale render'; exit 1"])
        self.calls(("as-1", {"summary": "Done.", "questions": []}), ("as-1", {"summary": "Still done.", "questions": []}))
        result = self.cli("start", "T-1", "--profile", str(self.profile_path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lint: stale render", result.stderr)
        self.assertEqual(card.read(self.root, "T-1")["state"], "failed")

    # A session that fails after an answer leaves the question open, not a card stuck in planning
    def test_failed_answer_session_keeps_the_question_open(self):
        self.analysis_profile()
        self.calls(("as-1", {"summary": "Facts collected.", "questions": ["Use the kit's Drawer?"]}))  # no second answer
        self.assertEqual(self.cli("start", "T-1", "--profile", str(self.profile_path)).returncode, 0)
        self.assertNotEqual(self.cli("decide", "T-1", "drawer", "Yes").returncode, 0)
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["questions"]), ("stopped", ["Use the kit's Drawer?"]))

    # status and journals read an analysis card without milestones or a plan, in another language too
    def test_status_and_journal_read_stopped_and_done_analysis_cards(self):
        self.profile["journal"] = [{"kind": "markdown", "dir": str(Path(self.directory.name) / "vault"), "page": "{key}/Friday.md"}]
        self.analysis_profile(check=["true"])
        self.calls(("as-1", {"summary": "Facts collected.", "questions": ["Use the kit's Drawer?"]}),
                   ("as-1", {"summary": "Documents written.", "questions": []}))
        self.assertEqual(self.cli("start", "T-1", "--profile", str(self.profile_path)).returncode, 0)
        for state in ("stopped", "done"):
            self.assertEqual(card.read(self.root, "T-1")["state"], state)
            for argv in (["status"], ["status", "T-1"], ["status", "T-1", "--json"], ["journal", "T-1"]):
                for lang in ("en", "ru"):
                    result = self.cli(*argv, lang=lang)
                    self.assertEqual(result.returncode, 0, (state, argv, lang, result.stderr))
            if state == "stopped":
                self.assertIn("Use the kit's Drawer?", self.cli("status", "T-1").stdout)
                self.assertEqual(self.cli("decide", "T-1", "drawer", "Yes").returncode, 0)
        self.assertTrue((Path(self.directory.name) / "vault" / "T-1" / "Friday.md").is_file())

    # decide on an analysis task without a waiting question is refused, and nothing is recorded
    def test_answer_without_a_question_is_refused(self):
        self.analysis_profile()
        self.calls(("as-1", {"summary": "Done.", "questions": []}))
        self.assertEqual(self.cli("start", "T-1", "--profile", str(self.profile_path)).returncode, 0)
        result = self.cli("decide", "T-1", "late", "Too late")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no analysis question", result.stderr)
        value = card.read(self.root, "T-1")
        self.assertEqual((value["state"], value["decisions"]), ("done", []))

    # The profile names its analysis command; plan keys are not needed
    def test_profile_validation(self):
        self.analysis_profile()
        self.assertEqual(profile.load(self.profile_path)["kind"], "analysis")
        for change, message in (({"kind": "review"}, "kind must be"),
                                ({"toolset": {"execute": "project"}}, "set toolset plan to project"),
                                ({"project": {"analysis_command": "/accept"}}, "with {key}"),
                                ({"project": {"analysis_command": "/accept {key}", "analysis_check": "lint"}}, "analysis_check")):
            data = dict({k: v for k, v in self.profile.items() if k != "path"}, **change)
            if "toolset" in change:
                data["project"]["plan_file"] = "PLAN.md"
                data["project"]["execute_command"] = "/run"
            self.profile_path.write_text(json.dumps(data))
            with self.assertRaises(FridayError) as error:
                profile.load(self.profile_path)
            self.assertIn(message, str(error.exception))


if __name__ == "__main__":
    unittest.main()
