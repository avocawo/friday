import json
import os
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

from friday import doctor
from friday.errors import FridayError


class DoctorTests(unittest.TestCase):
    def test_different_base_and_target_are_visible_without_blocking_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'profile.json'
            config.write_text(json.dumps({'name': 'test', 'repository': directory, 'base_branch': 'master',
                'autonomous_delivery': {'enabled': True, 'transport': 'local', 'target_branch': 'develop'}}))
            with patch('friday.doctor.gate.git', side_effect=FridayError('git_error', 'missing local ref')), \
                 patch('friday.doctor.autopilot.preflight'), patch('friday.doctor.isolation.require_runner'), \
                 patch('friday.doctor.isolation.require_browser'):
                result = doctor.inspect(config)
            check = next(item for item in result['checks'] if item['name'] == 'delivery_branches')
            self.assertEqual(check['status'], 'warning')
            self.assertIn('master', check['reason'])
            self.assertIn('develop', check['reason'])

    def test_unsupported_full_ci_prevents_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'profile.json'
            config.write_text(json.dumps({'name': 'test', 'repository': directory,
                                          'full_ci': {'kind': 'docker', 'image': 'missing'}}))
            with patch('friday.doctor.gate.git'), patch('friday.doctor.autopilot.preflight'), \
                 patch('friday.doctor.isolation.require_runner'), patch('friday.doctor.isolation.require_browser'):
                result = doctor.inspect(config)
            self.assertFalse(result['ready'])
            self.assertEqual(next(item['status'] for item in result['checks'] if item['name'] == 'full_ci'), 'unavailable')

    def test_diagnostics_do_not_expose_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": directory,
                                          "gitlab": {"token_env": "DOCTOR_TEST_TOKEN"}}))
            with patch.dict(os.environ, {"DOCTOR_TEST_TOKEN": "very-secret"}), \
                 patch("friday.doctor.gate.git", return_value=b".git"), \
                 patch("friday.doctor.autopilot.preflight"):
                result = doctor.inspect(config)
            self.assertFalse(result["ready"])
            self.assertEqual([check["status"] for check in result["checks"] if check["name"].endswith("_isolation")],
                             ["unavailable", "unavailable"])
            self.assertNotIn("very-secret", json.dumps(result))

    def test_agent_checks_do_not_return_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "zai.env"
            token.write_text("ZAI_API_KEY=private-value\n")
            token.chmod(0o600)
            config = Path(directory) / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": directory, "agents": {
                "a": {"vendor": "anthropic"}, "b": {"vendor": "openai"},
                "c": {"vendor": "zai", "token_file": str(token), "token_key": "ZAI_API_KEY"}}}))
            with patch("friday.doctor.gate.git", return_value=b".git"), \
                 patch("friday.doctor.autopilot.preflight"), patch("friday.doctor.shutil.which", return_value="cli"), \
                 patch("friday.doctor.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
                result = doctor.inspect(config)
            checks = {item["name"]: item["status"] for item in result["checks"]}
            self.assertEqual([checks[name] for name in ("agent_anthropic", "agent_openai", "agent_zai")], ["ok"] * 3)
            self.assertNotIn("private-value", json.dumps(result))

    def test_glab_read_auth_does_not_mask_missing_delivery_token(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"FRIDAY_GITLAB_TOKEN": ""}):
            config = Path(directory) / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": directory, "gitlab": {
                "backend": "glab", "base_url": "https://gitlab.example", "token_file": str(Path(directory) / "missing")}}))
            with patch("friday.doctor.gate.git", return_value=b".git"), patch("friday.doctor.autopilot.preflight"), \
                 patch("friday.doctor.shutil.which", return_value="glab"), \
                 patch("friday.doctor.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
                result = doctor.inspect(config)
            checks = {item["name"]: item["status"] for item in result["checks"]}
            self.assertEqual(checks["gitlab_credentials"], "ok")
            self.assertEqual(checks["gitlab_action_credentials"], "unavailable")


if __name__ == "__main__":
    unittest.main()
