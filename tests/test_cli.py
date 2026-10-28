import json
import os
import contextlib
import io
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


CLI = Path(__file__).resolve().parents[1] / "bin" / "friday"


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        with tempfile.TemporaryDirectory() as cwd:
            return subprocess.run(
                [sys.executable, str(CLI), *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_version_is_a_json_success_on_stdout(self):
        result = self.run_cli("--version")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {"ok": True, "data": {"version": "0.1.0"}})

    def test_help_and_empty_invocation_show_help(self):
        for args in ((), ("--help",), ("-h",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)

                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "")
                self.assertIn("usage: friday", result.stdout)
                self.assertIn("--version", result.stdout)

    def test_help_lists_friday_names_and_explains_external_actions(self):
        result = self.run_cli("--help")
        for name in ("clock-in (new)", "take-stock (today)", "home-stretch (go)", "keep-watch (eyes-up)",
                     "wrap-up (report)", "clear-desk (clean)", "send-off (suit-up)"):
            self.assertIn(name, result.stdout)
        self.assertIn("push the branch and create a draft MR", self.run_cli("home-stretch", "--help").stdout)
        self.assertIn("approved external action", self.run_cli("send-off", "--help").stdout)

    def test_new_names_and_legacy_aliases_reach_the_same_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": "."}))
            env = dict(os.environ, FRIDAY_HOME=str(root / "state"))
            def run(*args):
                return subprocess.run([sys.executable, str(CLI), *args], cwd=directory, env=env,
                                      capture_output=True, text=True)
            for command, key in (("clock-in", "T-1"), ("new", "T-2")):
                result = run(command, key, "--profile", str(config))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["data"]["key"], key)
            for primary, alias, args in (("take-stock", "today", ()), ("keep-watch", "eyes-up", ("--once",))):
                first, second = run(primary, *args), run(alias, *args)
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))
            for primary, alias, args in (("home-stretch", "go", ("MISSING-1",)),
                                         ("wrap-up", "report", ("MISSING-1",)),
                                         ("clear-desk", "clean", ("MISSING-1",)),
                                         ("send-off", "suit-up", ("MISSING-1", "missing"))):
                first, second = run(primary, *args), run(alias, *args)
                self.assertEqual(first.returncode, 1)
                self.assertEqual(json.loads(first.stderr), json.loads(second.stderr))

    def test_unknown_input_is_a_json_error_on_stderr(self):
        for args in (("unknown-command",), ("--unknown",), ("--version", "unexpected"),
                     ("--help", "--unknown"), ("--ver",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)

                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                error = json.loads(result.stderr)
                self.assertEqual(error["ok"], False)
                self.assertEqual(error["error"]["code"], "usage_error")
                self.assertTrue(error["error"]["message"])

    def test_eyes_up_with_no_tasks_is_quiet(self):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run([sys.executable, str(CLI), "eyes-up", "--once"], cwd=home,
                                    env={"FRIDAY_HOME": home}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["data"], {"results": [], "errors": {}})

    def test_eyes_up_reports_failed_poll_as_error(self):
        with tempfile.TemporaryDirectory() as home:
            config = Path(home) / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": ".", "gitlab": {
                "base_url": "https://example.invalid", "project": "p", "token_env": "FRIDAY_MISSING_TEST_TOKEN"}}))
            env = dict(os.environ, FRIDAY_HOME=home)
            env.pop("FRIDAY_MISSING_TEST_TOKEN", None)
            def run(*args):
                return subprocess.run([sys.executable, str(CLI), *args], cwd=home, env=env,
                                      capture_output=True, text=True)
            self.assertEqual(run("new", "T-1", "--profile", str(config)).returncode, 0)
            self.assertEqual(run("mr", "T-1", "1").returncode, 0)
            result = run("eyes-up", "--once")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "watch_failed")

    def test_service_command_dispatches_without_shadowing_module(self):
        module = runpy.run_path(str(CLI), run_name="friday_cli_test")
        with patch.object(module["service"], "install", return_value={"path": "test"}) as install, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = module["main"](["service", "install", "--interval", "300"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["data"], {"path": "test"})
        install.assert_called_once_with(300)


if __name__ == "__main__":
    unittest.main()
