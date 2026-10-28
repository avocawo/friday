import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import release, state
from friday.errors import FridayError


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        mock = patch("friday.isolation.require_release_build")
        mock.start()
        self.addCleanup(mock.stop)

    def test_build_reports_changed_dist_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            origin = root / "origin.git"
            repo = root / "repo"
            subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repo / "dist").mkdir()
            (repo / "dist" / "bundle.js").write_text("old")
            (repo / "package.json").write_text('{"version":"0.0.0"}')
            git("add", "dist/bundle.js", "package.json")
            git("commit", "-qm", "initial")
            git("branch", "-M", "master")
            git("remote", "add", "origin", str(origin))
            git("push", "-q", "origin", "master")
            git("branch", "-M", "friday/T-1")
            config = root / "profile.json"
            build = ["python3", "-c", "import pathlib,sys; pathlib.Path(sys.argv[1], 'dist/bundle.js').write_text('new')", "{worktree}"]
            config.write_text(json.dumps({"name": "test", "repository": str(repo), "release": {
                "cwd": ".", "base_branch": "master", "build": build}}))
            state.create(root, "T-1", str(config))
            state.update(root, "T-1", lambda card: card.update({"worktree": str(repo), "branch": "friday/T-1"}))
            with patch("friday.release.report.acceptance", return_value={"final_gate_passed": True}):
                result = release.prepare(root, "T-1", "1.2.3")
            self.assertEqual(result["changed_dist"], ["dist/bundle.js"])
            self.assertFalse(result["ready_for_tag"])
            self.assertEqual(json.loads((repo / "package.json").read_text())["version"], "0.0.0")
            self.assertEqual(git("ls-remote", "--tags", "origin"), "")

    def test_prerelease_or_shell_input_is_rejected(self):
        with self.assertRaises(FridayError) as error:
            release.prepare("/unused", "T-1", "1.2.3;echo")
        self.assertEqual(error.exception.code, "invalid_version")


if __name__ == "__main__":
    unittest.main()
