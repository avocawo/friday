import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from friday import gate, profile, stages, state


@unittest.skipUnless(os.environ.get("FRIDAY_DOCKER_TEST") == "1", "requires local alpine:3.20 image")
class DockerGateTests(unittest.TestCase):
    def test_source_is_read_only_in_real_container(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repo / "source").write_text("expected")
            git("add", "source")
            git("commit", "-qm", "initial")
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": str(repo), "gate_image": "alpine:3.20",
                                          "gate": ["sh", "-c", "test \"$(cat source)\" = expected && ! touch /workspace/source"]}))
            state.create(root, "T-1", str(config), mode="auto")
            stages.add(root, "T-1", "check")
            state.update(root, "T-1", lambda card: card["stages"]["check"].update(environment_lock=True))
            result = gate.execute(root, "T-1", "check", profile.load(config))
            self.assertEqual(result["status"], "passed", Path(result["log"]).read_text())
            self.assertEqual(stages.close(root, "T-1", "check", "gate")["stages"]["check"]["status"], "closed")


if __name__ == "__main__":
    unittest.main()
