import tempfile
import unittest
from pathlib import Path

from friday import codex_policy
from friday.errors import FridayError


class CodexPolicyTests(unittest.TestCase):
    def test_project_configuration_is_rejected_before_cli_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve() / "workspace"
            (workspace / ".codex").mkdir(parents=True)
            (workspace / ".codex" / "config.toml").write_text("# configured")
            with self.assertRaises(FridayError) as error:
                codex_policy.verify_environment("/does/not/exist", workspace)
            self.assertEqual(error.exception.code, "isolation_unavailable")

    def test_unexpected_mcp_server_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve() / "workspace"
            workspace.mkdir()
            executable = Path(directory) / "codex"
            executable.write_text("#!/bin/sh\nprintf '%s\\n' '[{\"name\":\"unexpected\"}]'\n")
            executable.chmod(0o755)
            with self.assertRaises(FridayError) as error:
                codex_policy.verify_environment(str(executable), workspace)
            self.assertEqual(error.exception.code, "isolation_unavailable")
