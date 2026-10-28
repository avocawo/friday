import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import credentials, service
from friday.errors import FridayError


class ServiceTests(unittest.TestCase):
    def test_refresh_adds_model_and_browser_executable_paths_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "LaunchAgents" / "watch.plist"
            binaries = {"codex": "/personal/bin/codex", "claude": "/node/bin/claude", "node": "/node/bin/node"}
            with patch("friday.service.sys.platform", "darwin"), patch("friday.service.agent_path", return_value=path), \
                 patch("friday.service.state.home", return_value=root / "state"), \
                 patch("friday.service.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                 patch("friday.service.shutil.which", side_effect=binaries.get):
                service.install(300)
                data = plistlib.loads(path.read_bytes())
                data["EnvironmentVariables"]["PATH"] = "/usr/bin"
                path.write_bytes(plistlib.dumps(data))
                self.assertTrue(service.refresh()["updated"])
                updated = plistlib.loads(path.read_bytes())
                self.assertEqual(updated["StartInterval"], 300)
                self.assertEqual(updated["EnvironmentVariables"]["PATH"].split(os.pathsep)[:2], ["/personal/bin", "/node/bin"])
                self.assertFalse(service.refresh()["updated"])

    def test_launch_agent_contains_no_token_and_can_be_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "LaunchAgents" / "watch.plist"
            def launch(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, "", "")
            with patch("friday.service.sys.platform", "darwin"), patch("friday.service.agent_path", return_value=path), \
                 patch("friday.service.state.home", return_value=root / "state"), \
                 patch("friday.service.subprocess.run", side_effect=launch), patch.dict(os.environ, {"FRIDAY_GITLAB_TOKEN": "secret"}):
                service.install(300)
                self.assertNotIn(b"secret", path.read_bytes())
                self.assertEqual(plistlib.loads(path.read_bytes())["StartInterval"], 300)
                service.uninstall()
            self.assertFalse(path.exists())

    def test_token_file_must_be_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text("secret\n")
            path.chmod(0o644)
            config = {"token_file": str(path), "token_env": "MISSING_TEST_TOKEN"}
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(FridayError) as error:
                    credentials.token(config, "MISSING_TEST_TOKEN")
                self.assertEqual(error.exception.code, "credentials_insecure")
                path.chmod(0o600)
                self.assertEqual(credentials.token(config, "MISSING_TEST_TOKEN"), "secret")

    def test_env_style_token_file_requires_exactly_one_key(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / "token.env"
            path.write_text('ZAI_API_KEY="synthetic"\nZAI_MODEL=example\n')
            path.chmod(0o600)
            config = {"token_file": str(path), "token_key": "ZAI_API_KEY"}
            self.assertEqual(credentials.token(config, "MISSING_TEST_TOKEN"), "synthetic")
            path.write_text('ZAI_API_KEY=one\nZAI_API_KEY=two\n')
            with self.assertRaises(FridayError) as error:
                credentials.token(config, "MISSING_TEST_TOKEN")
            self.assertEqual(error.exception.code, "credentials_insecure")

    def test_tracker_organization_uses_owner_only_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / "org"
            path.write_text("123\n")
            path.chmod(0o600)
            config = {"org_file": str(path), "org_env": "TEST_ORG"}
            self.assertEqual(credentials.organization(config), "123")
            path.chmod(0o644)
            with self.assertRaises(FridayError) as error:
                credentials.organization(config)
            self.assertEqual(error.exception.code, "credentials_insecure")


if __name__ == "__main__":
    unittest.main()
