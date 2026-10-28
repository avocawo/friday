import unittest
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch

from friday import gate, isolation, release
from friday.errors import FridayError


class IsolationTests(unittest.TestCase):
    def test_browser_without_profile_and_release_stop_before_running_external_processes(self):
        operations = [lambda: isolation.require_browser(), lambda: release.prepare("missing", "T-1", "1.0.0")]
        with patch("subprocess.run") as process:
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaises(FridayError) as error:
                        operation()
                    self.assertEqual(error.exception.code, "isolation_unavailable")
            process.assert_not_called()

    def test_browser_requires_a_successful_live_probe(self):
        selected = {"browser": {"mcp_server": "server", "stage_origin": "https://gql.stage.example"}}
        with patch("friday.browser_probe.verify") as probe:
            isolation.require_browser(selected)
            probe.assert_called_once_with(selected)

    def test_unknown_runner_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as directory, patch("friday.isolation.sys.platform", "darwin"), \
             patch("subprocess.run") as process:
            selected = {"runners": {"plan": {"command": ["python3", "untrusted.py"]}}}
            with self.assertRaises(FridayError) as error:
                isolation.require_runner(selected, directory)
            self.assertEqual(error.exception.code, "isolation_unavailable")
            process.assert_not_called()

    def test_git_stops_before_running_an_external_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", directory], check=True)
            subprocess.run(["git", "-C", directory, "config", "filter.probe.clean", "touch marker"], check=True)
            with self.assertRaises(FridayError) as error:
                gate.git(repo, "status", "--porcelain")
            self.assertEqual(error.exception.code, "isolation_unavailable")
            self.assertFalse((repo / "marker").exists())
