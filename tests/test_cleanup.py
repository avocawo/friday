import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import cleanup, state
from friday.errors import FridayError


class CleanupTests(unittest.TestCase):
    def test_cleanup_requires_current_confirmation_including_ignored_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repo / ".gitignore").write_text("ignored/\n")
            (repo / "source").write_text("source\n")
            git("add", ".gitignore", "source")
            git("commit", "-qm", "initial")
            worktree = root / "worktree"
            git("worktree", "add", "-b", "friday/T-1", str(worktree))
            (worktree / "ignored").mkdir()
            target = worktree / "ignored" / "data"
            target.write_text("first")
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": str(repo)}))
            state.create(root, "T-1", str(config))
            state.update(root, "T-1", lambda card: card.update({"worktree": str(worktree), "branch": "friday/T-1"}))
            preview = cleanup.inspect(root, "T-1")
            self.assertEqual(preview["ignored"][0]["path"], "ignored/data")
            target.write_text("second")
            with self.assertRaises(FridayError) as error:
                cleanup.remove(root, "T-1", preview["confirmation"])
            self.assertEqual(error.exception.code, "stale_approval")
            self.assertTrue(target.exists())
            git("worktree", "lock", str(worktree))
            current = cleanup.inspect(root, "T-1")
            with self.assertRaises(FridayError) as error:
                cleanup.remove(root, "T-1", current["confirmation"])
            self.assertEqual(error.exception.code, "cleanup_failed")
            target.write_text("changed after failure")
            cleanup.cancel(root, "T-1")
            git("worktree", "unlock", str(worktree))
            current = cleanup.inspect(root, "T-1")
            original_update = state.update
            def interrupted(*args, **kwargs):
                if not worktree.exists():
                    raise OSError("simulated crash after removal")
                return original_update(*args, **kwargs)
            with patch("friday.cleanup.state.update", side_effect=interrupted):
                with self.assertRaises(OSError):
                    cleanup.remove(root, "T-1", current["confirmation"])
            self.assertFalse(worktree.exists())
            cleanup.remove(root, "T-1", current["confirmation"])
            self.assertFalse(worktree.exists())
            self.assertNotIn("worktree", state.read(root, "T-1"))


if __name__ == "__main__":
    unittest.main()
