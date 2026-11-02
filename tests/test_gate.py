import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import gate, profile, stages, state
from friday.errors import FridayError
from friday.locks import locked


class GateTests(unittest.TestCase):
    def test_private_gate_output_is_not_forwarded_to_model(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'gate.log'
            log.write_text('test failure for synthetic@example.invalid')
            feedback = gate.feedback({'log': str(log)})
            self.assertIn('not forwarded', feedback)
            self.assertNotIn('synthetic@example.invalid', feedback)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "code.txt").write_text("one")
        self.git("add", "code.txt")
        self.git("commit", "-qm", "initial")
        self.config = self.root / "profile.json"
        self.config.write_text(json.dumps({"name": "test", "repository": str(self.repo),
                                           "gate_image": "test-image", "gate": ["test-command"]}))
        state.create(self.root, "T-1", str(self.config))
        stages.add(self.root, "T-1", "build")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def docker(self, command, **kwargs):
        if command[0] != "docker":
            return self.real_run(command, **kwargs)
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "sha256:123\n", "")
        self.assertIn("--read-only", command)
        self.assertIn("none", command)
        self.assertEqual(command[command.index("--entrypoint") + 1], "test-command")
        self.assertTrue(any(arg.endswith("dst=/workspace,readonly") for arg in command))
        return subprocess.CompletedProcess(command, 0, "passed\n")

    def test_isolated_gate_and_closing_reject_changed_code(self):
        self.real_run = subprocess.run
        with patch("friday.gate.subprocess.run", side_effect=self.docker):
            result = gate.execute(self.root, "T-1", "build", profile.load(self.config))
            self.assertEqual(result["status"], "passed")
            event = state.read(self.root, 'T-1')['events'][-1]
            self.assertEqual(event['record_sha256'], gate.record_digest(result))
            (self.repo / "code.txt").write_text("changed")
            with self.assertRaises(FridayError) as error:
                stages.close(self.root, "T-1", "build", "gate")
            self.assertEqual(error.exception.code, "dirty_tree")
            self.git("checkout", "--", "code.txt")
            result = stages.close(self.root, "T-1", "build", "gate")
            self.assertEqual(result["stages"]["build"]["status"], "closed")

    def test_gate_rejects_dirty_worktree(self):
        (self.repo / "new.txt").write_text("untracked")
        with self.assertRaises(FridayError) as error:
            gate.snapshot(profile.load(self.config))
        self.assertEqual(error.exception.code, "dirty_tree")

    def test_close_requires_authenticated_successful_gate_even_without_review_binding(self):
        self.real_run = subprocess.run
        with patch('friday.gate.subprocess.run', side_effect=self.docker):
            gate.execute(self.root, 'T-1', 'build', profile.load(self.config))
            original = state.read(self.root, 'T-1')
            for change in ('missing_digest', 'wrong_digest', 'failed_event', 'missing_event', 'bad_exit', 'changed_snapshot'):
                with self.subTest(change=change):
                    def alter(card):
                        card.clear()
                        card.update(json.loads(json.dumps(original)))
                        event = card['events'][-1]
                        record = card['stages']['build']['gate']
                        if change == 'missing_digest': event.pop('record_sha256')
                        elif change == 'wrong_digest': event['record_sha256'] = 'wrong'
                        elif change == 'failed_event': event['status'] = 'failed'
                        elif change == 'missing_event': card['events'].pop()
                        elif change == 'bad_exit':
                            record['exit_code'] = 1
                            event['record_sha256'] = gate.record_digest(record)
                        else:
                            record['unchanged'] = False
                            event['record_sha256'] = gate.record_digest(record)
                    state.update(self.root, 'T-1', alter)
                    with self.assertRaises(FridayError) as error:
                        stages.close(self.root, 'T-1', 'build', 'gate')
                    self.assertEqual(error.exception.code, 'stale_evidence')
            # An actual fresh run replaces legacy or invalid provenance.
            gate.execute(self.root, 'T-1', 'build', profile.load(self.config))
            self.assertEqual(stages.close(self.root, 'T-1', 'build', 'gate')['stages']['build']['status'], 'closed')

    def test_private_tmp_is_a_separate_ephemeral_mount_in_gate_definition(self):
        self.real_run = subprocess.run
        selected = profile.load(self.config)
        selected['gate_private_tmp'] = True
        commands = []
        def docker(command, **kwargs):
            if command[:2] == ['docker', 'run']:
                commands.append(command)
            return self.docker(command, **kwargs)
        with patch('friday.gate.subprocess.run', side_effect=docker):
            self.assertEqual(gate.execute(self.root, 'T-1', 'build', selected)['status'], 'passed')
        self.assertIn('/private/tmp', commands[0])
        self.assertNotEqual(gate.definition(selected, 'image'), gate.definition(profile.load(self.config), 'image'))

    def test_executable_tmp_requires_opt_in_and_changes_gate_definition(self):
        self.real_run = subprocess.run
        selected = profile.load(self.config)
        selected.update(gate_private_tmp=True, gate_exec_tmp=True)
        commands = []
        def docker(command, **kwargs):
            if command[:2] == ['docker', 'run']:
                commands.append(command)
            return self.docker(command, **kwargs)
        with patch('friday.gate.subprocess.run', side_effect=docker):
            self.assertEqual(gate.execute(self.root, 'T-1', 'build', selected)['status'], 'passed')
        self.assertIn('/tmp:rw,exec,nosuid,nodev', commands[0])
        self.assertIn('/private/tmp:rw,exec,nosuid,nodev', commands[0])
        self.assertNotEqual(gate.definition(selected, 'image'), gate.definition(profile.load(self.config), 'image'))
        selected['gate_exec_tmp'] = 'yes'
        with self.assertRaises(FridayError) as error:
            gate.execute(self.root, 'T-1', 'build', selected)
        self.assertEqual(error.exception.code, 'invalid_profile')

    def test_git_archive_export_ignore_is_rejected(self):
        (self.repo / ".gitattributes").write_text("code.txt export-ignore\n")
        self.git("add", ".gitattributes")
        self.git("commit", "-qm", "archive attributes")
        with self.assertRaises(FridayError) as error:
            gate.snapshot(profile.load(self.config))
        self.assertEqual(error.exception.code, "incomplete_snapshot")

    def test_document_and_owner_are_distinct_evidence(self):
        evidence = self.root / "verdict.txt"
        evidence.write_text("VERDICT: fail\n")
        with self.assertRaises(FridayError):
            stages.close(self.root, "T-1", "build", "document", str(evidence))
        evidence.write_text("checked\nVERDICT: pass\n")
        card = stages.close(self.root, "T-1", "build", "document", str(evidence))
        self.assertIn("sha256:", card["stages"]["build"]["evidence"])

    def test_unavailable_image_requires_owner_to_close(self):
        self.real_run = subprocess.run
        def no_image(command, **kwargs):
            if command[0] == "docker":
                return subprocess.CompletedProcess(command, 1, "", "missing")
            return self.real_run(command, **kwargs)
        with patch("friday.gate.subprocess.run", side_effect=no_image):
            with self.assertRaises(FridayError):
                gate.execute(self.root, "T-1", "build", profile.load(self.config))
        evidence = self.root / "verdict.txt"
        evidence.write_text("VERDICT: pass\n")
        with self.assertRaises(FridayError) as error:
            stages.close(self.root, "T-1", "build", "document", str(evidence))
        self.assertEqual(error.exception.code, "owner_required")
        self.assertEqual(stages.close(self.root, "T-1", "build", "owner", "accepted")["stages"]["build"]["status"], "closed")

    def test_close_waits_for_gate_resource(self):
        finished = threading.Event()
        def close():
            stages.close(self.root, "T-1", "build", "owner", "accepted")
            finished.set()
        with locked(self.root, "resource:branch:" + str(self.repo.resolve())):
            worker = threading.Thread(target=close)
            worker.start()
            self.assertFalse(finished.wait(0.2))
        worker.join(5)
        self.assertTrue(finished.is_set())


if __name__ == "__main__":
    unittest.main()
