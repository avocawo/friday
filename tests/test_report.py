import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import gate, report, state


class ReportTests(unittest.TestCase):
    def test_final_gate_is_not_reported_as_fresh_after_gate_definition_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repo / "source").write_text("code")
            git("add", "source")
            git("commit", "-qm", "initial")
            head = git("rev-parse", "HEAD")
            config = root / "profile.json"
            original = {"name": "test", "repository": str(repo), "gate_image": "image", "gate": ["old"]}
            config.write_text(json.dumps(original))
            state.create(root, "T-1", str(config))
            record = {"status": "passed", "head": head, "snapshot": "snapshot", "definition": gate.definition(original, "image-id")}
            state.update(root, "T-1", lambda card: card["stages"].update({"final": {"status": "closed", "gate": record,
                                                                                   "evidence": "gate: snapshot", "criteria": ["works"]}}))
            with patch("friday.report.gate.snapshot", return_value=(head, "snapshot", b"")), \
                 patch("friday.report.gate.image_id", return_value="image-id"):
                self.assertTrue(report.acceptance(root, "T-1")["final_gate_passed"])
                original["gate"] = ["changed"]
                config.write_text(json.dumps(original))
                result = report.acceptance(root, "T-1")
            self.assertFalse(result["final_gate_passed"])
            self.assertEqual(result["criteria"][0]["status"], "stale")


if __name__ == "__main__":
    unittest.main()
