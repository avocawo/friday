import os
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
        self.assertEqual(self.run_gate(125, "FRIDAY_ENVIRONMENT_UNAVAILABLE: image lockfile differs")["status"], "environment_unavailable")
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

    def test_image_dependencies_are_linked_and_caches_are_writable(self):
        captured = {}
        def run(command, **kwargs):
            workspace = next(part for part in command if "dst=/workspace" in part).split(",")[1][4:]
            captured["links"] = {name: os.readlink(os.path.join(workspace, name)) for name in (".venv", "node_modules")}
            captured["command"] = command
            return subprocess.CompletedProcess(command, 0, "")
        archive = gate.snapshot(self.profile)[2]
        profile = dict(self.profile, gate_links={".venv": "/opt/venv"}, gate_node_modules="/app/node_modules",
                       gate_tmpfs=["/opt/venv/.cache"])
        with patch("friday.gate.subprocess.run", side_effect=run):
            gate.container(profile, "sha256:x", "HEAD", archive)
        self.assertEqual(captured["links"], {".venv": "/opt/venv", "node_modules": "/app/node_modules"})
        self.assertIn("--tmpfs /opt/venv/.cache", " ".join(captured["command"]))
        for bad in ({"gate_links": {"../x": "/opt"}}, {"gate_links": {"x": "relative"}}, {"gate_tmpfs": ["/workspace/cache"]},
                    {"gate_tmpfs": ["cache"]}):
            with self.assertRaises(FridayError):
                gate.container(dict(self.profile, **bad), "sha256:x", "HEAD", archive)

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


if __name__ == "__main__":
    unittest.main()
