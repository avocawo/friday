import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import gate
from friday.errors import FridayError


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class GateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@t")
        (self.repo / "source").write_text("expected\n")
        git(self.repo, "add", "source")
        git(self.repo, "commit", "-qm", "init")
        self.profile = {"repository": str(self.repo), "gate": ["check"], "gate_image": "img",
                        "environment_errors": ["Could not resolve host"]}

    def run_gate(self, code, output, change=None):
        def container(profile, image, head, archive):
            if change:
                change()
            return subprocess.CompletedProcess([], code, output)
        with patch("friday.gate.image_id", return_value="sha256:x"), patch("friday.gate.container", side_effect=container):
            return gate.check(self.root, self.profile, self.repo, None, self.root / "logs" / "gate.log")

    def test_status_follows_exit_code_environment_and_snapshot(self):
        passed = self.run_gate(0, "ok")
        self.assertEqual((passed["status"], passed["head"]), ("passed", git(self.repo, "rev-parse", "HEAD")))
        self.assertEqual(Path(passed["log"]).read_text(), "ok")
        self.assertEqual(self.run_gate(1, "error TS2307")["status"], "failed")
        self.assertEqual(self.run_gate(1, "Could not resolve host registry")["status"], "environment_unavailable")
        self.assertEqual(self.run_gate(125, "FRIDAY_IMAGE_STALE: image lockfile differs")["status"], "image_stale")
        moved = self.run_gate(0, "ok", change=lambda: (self.repo / "source").write_text("changed\n"))
        self.assertEqual((moved["status"], moved["unchanged"]), ("failed", False))

    def test_dirty_worktree_and_export_ignore_are_rejected(self):
        (self.repo / "source").write_text("dirty\n")
        with self.assertRaises(FridayError) as error:
            self.run_gate(0, "ok")
        self.assertEqual(error.exception.code, "dirty_tree")
        git(self.repo, "checkout", "--", "source")
        (self.repo / ".gitattributes").write_text("source export-ignore\n")
        git(self.repo, "add", ".gitattributes")
        git(self.repo, "commit", "-qm", "attributes")
        with self.assertRaises(FridayError) as error:
            self.run_gate(0, "ok")
        self.assertEqual(error.exception.code, "incomplete_snapshot")

    def test_git_diff_mode_needs_a_base_of_the_head(self):
        self.profile["gate_mode"] = "git-diff"
        with self.assertRaises(FridayError):
            gate.check(self.root, self.profile, self.repo, None, self.root / "gate.log")

    def test_container_is_read_only_and_networkless(self):
        captured = {}
        def run(command, **kwargs):
            captured["command"] = command
            return subprocess.CompletedProcess(command, 0, "")
        archive = gate.snapshot(self.profile)[2]
        with patch("friday.gate.subprocess.run", side_effect=run):
            gate.container(dict(self.profile, gate_private_tmp=True), "sha256:x", "HEAD", archive)
        command = " ".join(captured["command"])
        for expected in ("--network none", "--read-only", "dst=/workspace,readonly", "--tmpfs /private/tmp", "--pull=never"):
            self.assertIn(expected, command)
        self.assertNotIn("exec", command.split("--tmpfs /tmp")[1].split()[0])  # /tmp is not executable without opt-in

    def test_failures_keep_failed_sections_and_drop_passing_output(self):
        log = ("\x1b[36m—→ ESLint\x1b[0m\n  3:7  error  'unref' no-shadow\n✖ 1 problem (1 error, 0 warnings)\n"
               "\x1b[36m—→ TypeScript\x1b[0m\nsrc/b.ts(4,1): error TS6133: unused\n"
               "\x1b[36m—→ Tests\x1b[0m\n" + "\n".join(" ✓ test %d" % i for i in range(5000)) + "\n Test Files 300 passed\n"
               "\x1b[31m✗ 2 of 6 checks failed.\x1b[0m")
        text = gate.failures(log)
        self.assertIn("no-shadow", text)
        self.assertIn("TS6133", text)
        self.assertNotIn("test 4999", text)
        plain = gate.failures("line\n" * 50 + "Error: boom\n" + "line\n" * 50)
        self.assertIn("Error: boom", plain)
        self.assertLess(len(plain.splitlines()), 10)

    def test_failures_longSectionWithTapFailureInTheMiddle_keepsTheFailedTestAndItsDiagnostics(self):
        build = "\n".join("building module %d, no error found" % i for i in range(400))
        passing = "\n".join("ok %d Chrome - [1 ms] - Unit | test %d" % (i, i) for i in range(1, 1023))
        failed = ("not ok 1023 Chrome - [2 ms] - Unit | Utils | replace: MarksNextAsReplaceChain\n    ---\n"
                  "        actual: >\n            true\n        expected: >\n            false\n"
                  "        message: >\n            chain continues\n    ...")
        after = "\n".join("ok %d Chrome - [1 ms] - Unit | test %d" % (i, i) for i in range(1024, 3000))
        log = "—→ lint\n—→ test\n" + build + "\n" + passing + "\n" + failed + "\n" + after + "\n# fail  1\n"
        text = gate.failures(log)
        self.assertIn(failed, text)  # the test name, actual and expected survive a section far over the budget
        self.assertLessEqual(len(text), 12000)

    def test_python_failure_feedback_preserves_the_traceback_body(self):
        body = ('  File "/workspace/tests/test_export.py", line 18, in setUp\n'
                '    self.git("add", "source.py")\n'
                '  File "/usr/lib/python3/subprocess.py", line 571, in run\n'
                '    raise CalledProcessError(retcode, process.args)\n'
                'subprocess.CalledProcessError: git add returned exit status 128.\n')
        log = 'passing output\n' * 100 + 'ERROR: test_export\n--------\nTraceback (most recent call last):\n' + body + '\nRan 1 test\nFAILED (errors=1)\n'
        self.assertIn(body.strip(), gate.failures(log))


if __name__ == "__main__":
    unittest.main()
