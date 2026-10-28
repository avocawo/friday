import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from friday import gate, profile, stages, state


@unittest.skipUnless(os.environ.get("FRIDAY_GIT_DIFF_TEST_IMAGE"), "requires an isolated test image")
class GitDiffGateTests(unittest.TestCase):
    def test_gate_sees_diff_against_saved_base_with_read_only_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repo / "source").write_text("old")
            (repo / ".gitignore").write_text("forced.ts\n")
            git("add", "source", ".gitignore")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (repo / "source").write_text("new")
            (repo / "forced.ts").write_text("forced\n")
            git("add", "source")
            git("add", "-f", "forced.ts")
            git("commit", "-qm", "change")
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": str(repo), "gate_mode": "git-diff",
                                          "gate_image": os.environ["FRIDAY_GIT_DIFF_TEST_IMAGE"], "gate_node_modules": "/app/node_modules",
                                          "gate_tmpfs": ["/app/node_modules/.vite-temp"],
                                          "gate": ["sh", "-c", "touch node_modules/.vite-temp/check && git diff --name-only HEAD | grep -qx forced.ts && git diff --name-only HEAD | grep -qx source && ! touch /workspace/source"]}))
            state.create(root, "T-1", str(config))
            state.update(root, "T-1", lambda card: card.update({"plan_base": base}))
            stages.add(root, "T-1", "check")
            result = gate.execute(root, "T-1", "check", profile.load(config))
            self.assertEqual(result["status"], "passed", Path(result["log"]).read_text())
            self.assertEqual(stages.close(root, "T-1", "check", "gate")["stages"]["check"]["status"], "closed")
            changed = json.loads(config.read_text())
            changed["gate_lockfile"] = "source"
            config.write_text(json.dumps(changed))
            stages.add(root, "T-1", "mismatch")
            mismatch = gate.execute(root, "T-1", "mismatch", profile.load(config))
            self.assertEqual(mismatch["status"], "environment_unavailable")
            self.assertIn("FRIDAY_ENVIRONMENT_UNAVAILABLE", Path(mismatch["log"]).read_text())


if __name__ == "__main__":
    unittest.main()
