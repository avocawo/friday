import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from friday import runners

ADAPTER = Path(__file__).resolve().parents[1] / "adapters" / "claude.py"


class ClaudeAdapterTests(unittest.TestCase):
    def test_runner_resolves_both_builtin_adapters(self):
        for placeholder, filename in (("{codex_adapter}", "codex.py"), ("{claude_adapter}", "claude.py")):
            with self.subTest(placeholder=placeholder):
                args = runners.command({"command": ["python3", placeholder]})
                self.assertEqual(Path(args[1]), ADAPTER.with_name(filename))

    def test_restricted_vendor_configuration_and_structured_review(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "claude"
            binary.write_text("#!/usr/bin/env python3\nimport json,os,sys\n"
                              "prompt=sys.stdin.read()\n"
                              "print(json.dumps({'is_error':False,'structured_output':{'verdict':'pass','findings':[],"
                              "'evidence':[json.dumps(sys.argv), os.environ.get('ANTHROPIC_BASE_URL','default'),"
                              "'token-present' if os.environ.get('ANTHROPIC_AUTH_TOKEN') else 'no-token',prompt]}}))\n")
            binary.chmod(0o755)
            for vendor in ("anthropic", "zai"):
                with self.subTest(vendor=vendor):
                    env = dict(os.environ, PATH=directory + os.pathsep + os.environ.get("PATH", ""),
                               FRIDAY_ZAI_TOKEN="synthetic-key", ANTHROPIC_BASE_URL="https://wrong.invalid")
                    request = {"role": "review", "vendor": vendor, "model": "example", "brief": "check",
                               "engineering_policy": ["Use the project precedent."]}
                    result = subprocess.run([sys.executable, str(ADAPTER)], input=json.dumps(request), text=True,
                                            capture_output=True, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    evidence = json.loads(result.stdout)["evidence"]
                    args = json.loads(evidence[0])
                    self.assertIn("--restricted", args)
                    self.assertIn("--safe-mode", args)
                    self.assertIn("--strict-mcp-config", args)
                    self.assertIn("--no-chrome", args)
                    settings = json.loads(args[args.index("--settings") + 1])
                    self.assertIn("Read(.env)", settings["permissions"]["deny"])
                    self.assertIn("mcp__*", settings["permissions"]["deny"])
                    self.assertNotIn("synthetic-key", result.stdout)
                    self.assertEqual(evidence[1], "https://api.z.ai/api/anthropic" if vendor == "zai" else "default")
                    self.assertEqual(evidence[2], "token-present" if vendor == "zai" else "no-token")
                    self.assertIn("Use the project precedent.", evidence[3])
                    self.assertIn("Apply the supplied engineering_policy", evidence[3])

    def test_planning_returns_structured_team_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "claude"
            binary.write_text("#!/usr/bin/env python3\nimport json,sys\n"
                              "schema=json.loads(sys.argv[sys.argv.index('--json-schema')+1])\n"
                              "assert 'crew' in schema['properties']['stages']['items']['properties']\n"
                              "print(json.dumps({'is_error':False,'structured_output':{'stages':[], 'questions':[], 'suggested_issues':[]}}))\n")
            binary.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ.get("PATH", ""))
            request = {"role": "plan", "vendor": "anthropic", "model": "example", "available_agents": {}}
            result = subprocess.run([sys.executable, str(ADAPTER)], input=json.dumps(request), text=True,
                                    capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["stages"], [])
