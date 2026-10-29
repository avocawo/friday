import json
import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import project, state
from friday.errors import FridayError


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.profile = self.root / "profile.json"
        self.profile.write_text(json.dumps({"name": "test", "repository": ".", "worktree_root": "trees", "base_branch": "main"}))
        self.brief = self.root / "brief.md"
        self.brief.write_text("Build a small local application.\n", encoding="utf-8")

    def test_start_creates_auto_card_and_passes_brief_to_existing_workflow(self):
        with patch("friday.project.intake.prepare") as prepare, patch("friday.project.autopilot.run", return_value={"completed": [], "remaining": []}) as run:
            result = project.start(self.root, "PROJECT-1", self.profile, self.brief)
        card = state.read(self.root, "PROJECT-1")
        self.assertEqual(card["ticket"]["description"], self.brief.read_text())
        self.assertEqual(card["ticket"]["kind"], "project")
        self.assertEqual(card["mode"], "auto")
        prepare.assert_called_once()
        run.assert_called_once_with(self.root, "PROJECT-1")
        self.assertEqual(result["remaining"], [])

    def test_interrupted_preparation_can_be_resumed_without_replacing_brief(self):
        with patch("friday.project.intake.prepare", side_effect=FridayError("git_error", "offline")):
            with self.assertRaises(FridayError):
                project.start(self.root, "PROJECT-1", self.profile, self.brief)
        self.brief.write_text("Changed after start", encoding="utf-8")
        with patch("friday.project.intake.prepare"), patch("friday.project.autopilot.run", return_value={"completed": [], "remaining": []}):
            project.run(self.root, "PROJECT-1")
        self.assertEqual(state.read(self.root, "PROJECT-1")["ticket"]["description"], "Build a small local application.\n")

    def test_regular_task_cannot_be_run_as_project(self):
        state.create(self.root, "TASK-1", self.profile, {"summary": "Task"}, "auto")
        with self.assertRaises(FridayError) as error:
            project.run(self.root, "TASK-1")
        self.assertEqual(error.exception.code, "not_project")

    def test_configured_delivery_uses_existing_mission_and_marks_it_for_watcher(self):
        with patch("friday.project.intake.prepare"), patch("friday.project.mission.config_for", return_value={"enabled": True}), \
             patch("friday.project.mission.advance", return_value={"active": True}) as advance:
            project.start(self.root, "PROJECT-1", self.profile, self.brief)
        self.assertTrue(state.read(self.root, "PROJECT-1")["autonomous_requested"])
        advance.assert_called_once_with(self.root, "PROJECT-1")

    def test_invalid_brief_does_not_create_card(self):
        self.brief.write_text("  ")
        with self.assertRaises(FridayError) as error:
            project.start(self.root, "PROJECT-1", self.profile, self.brief)
        self.assertEqual(error.exception.code, "invalid_brief")
        self.assertFalse(state.task_path(self.root, "PROJECT-1").exists())

    def test_backlog_does_not_select_new_work_while_own_pull_is_open(self):
        state.create(self.root, 'PROJ-1', str(self.profile.resolve()), {'kind': 'github_issue', 'number': 1}, 'auto')
        state.update(self.root, 'PROJ-1', lambda card: card.update(merge_request=10))
        self.profile.write_text(json.dumps({'name': 'test', 'repository': '.', 'worktree_root': 'trees', 'base_branch': 'main',
                                            'github': {'repository': 'a/b'}, 'autonomous_delivery': {
                                                'enabled': True, 'transport': 'github', 'target_branch': 'main'}}))
        with patch('friday.github.check_delivery'), patch('friday.github.get_pull', return_value={'state': 'opened'}), \
             patch('friday.github.select_issue') as select, patch('friday.mission.advance', return_value={'processed': 0}) as advance:
            result = project.next_issue(self.root, self.profile)
        self.assertEqual(result['selected'], 'PROJ-1')
        self.assertTrue(result['active'])
        select.assert_not_called()
        advance.assert_called_once()

    def test_backlog_with_no_eligible_work_is_read_only(self):
        self.profile.write_text(json.dumps({'name': 'test', 'repository': '.', 'worktree_root': 'trees', 'base_branch': 'main',
                                            'github': {'repository': 'a/b'}, 'autonomous_delivery': {
                                                'enabled': True, 'transport': 'github', 'target_branch': 'main'}}))
        with patch('friday.github.check_delivery'), patch('friday.github.select_issue', return_value=None):
            self.assertEqual(project.next_issue(self.root, self.profile), {'selected': None})
        self.assertEqual(state.all_tasks(self.root), [])

    def test_cli_returns_json_for_invalid_brief(self):
        self.brief.write_text("")
        cli = Path(__file__).resolve().parents[1] / "bin" / "friday"
        result = subprocess.run([sys.executable, str(cli), "long-weekend", "start", "PROJECT-1", "--profile", str(self.profile), "--brief", str(self.brief)],
                                env=dict(os.environ, FRIDAY_HOME=str(self.root / "home")), text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_brief")

    def test_cli_help_discloses_possible_delivery(self):
        cli = Path(__file__).resolve().parents[1] / "bin" / "friday"
        for subcommand in ("start", "resume"):
            result = subprocess.run([sys.executable, str(cli), "long-weekend", subcommand, "--help"],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertIn("push a branch and create a draft MR", " ".join(result.stdout.split()))

    @unittest.skipUnless(os.environ.get("FRIDAY_DOCKER_TEST") == "1", "requires local friday-test:local image")
    def test_project_runs_a_plan_in_a_local_worktree_with_fake_models(self):
        repo, remote = self.root / "repo", self.root / "remote.git"
        repo.mkdir()
        def git(*args):
            return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout
        git("init", "--bare", str(remote))
        git("-C", str(repo), "init", "-b", "main")
        git("-C", str(repo), "config", "user.name", "Test")
        git("-C", str(repo), "config", "user.email", "test@example.invalid")
        (repo / "source").write_text("old\n")
        git("-C", str(repo), "add", "source")
        git("-C", str(repo), "commit", "-m", "initial")
        git("-C", str(repo), "remote", "add", "origin", str(remote))
        git("-C", str(repo), "push", "origin", "main")
        runner = self.root / "runner.py"
        runner.write_text("import json,pathlib,sys\nr=json.load(sys.stdin)\n"
                          "if r['role']=='plan': print(json.dumps({'stages':[{'id':'one','brief':'update','criteria':['source is new'],'files':['source']}]}))\n"
                          "elif r['role']=='develop': pathlib.Path('source').write_text('new\\n'); print('done')\n"
                          "else: print(json.dumps({'verdict':'pass','evidence':['observed']}))\n")
        runners = {role: {"model": "dev" if role in ("plan", "develop") else "review", "command": [sys.executable, str(runner)]}
                   for role in ("plan", "develop", "review", "blind-code", "blind-behavior")}
        self.profile.write_text(json.dumps({"name": "test", "repository": str(repo), "worktree_root": str(self.root / "trees"),
                                            "base_branch": "main", "runners": runners, "gate_image": "friday-test:local",
                                            "gate": ["python3", "-c", "assert open('source').read() == 'new\\n'"]}))
        with patch("friday.isolation.require_runner"), patch("friday.isolation.require_browser"), \
             patch("friday.isolated_preview.open_preview", side_effect=lambda *_: contextlib.nullcontext("http://test.localhost:1234/")):
            result = project.start(self.root, "PROJECT-1", self.profile, self.brief)
        card = state.read(self.root, "PROJECT-1")
        self.assertEqual(result["remaining"], [])
        self.assertEqual(card["stages"]["one"]["status"], "closed")
        self.assertEqual((Path(card["worktree"]) / "source").read_text(), "new\n")
        self.assertEqual((repo / "source").read_text(), "old\n")
