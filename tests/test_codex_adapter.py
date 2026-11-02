import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from friday import schemas

ADAPTER = Path(__file__).resolve().parents[1] / "adapters" / "codex.py"


class CodexAdapterTests(unittest.TestCase):
    def test_behavior_names_scoped_browser_and_configures_only_its_tools(self):
        from friday.browser_session import TOOLS
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / 'codex'
            fake.write_text("#!/usr/bin/env python3\nimport json,sys\n"
                            "if sys.argv[1:4] == ['mcp','list','--json']:\n print('[]'); sys.exit(0)\n"
                            "p=sys.argv[sys.argv.index('--output-last-message')+1]\n"
                            "open(p,'w').write(json.dumps({'verdict':'unverifiable','findings':[json.dumps(sys.argv),sys.stdin.read()],'evidence':[]}))\n")
            fake.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ.get('PATH', ''),
                       FRIDAY_BROWSER_CONFIG=json.dumps({'mode': 'local'}))
            request = {'role': 'blind-behavior', 'model': 'review-model',
                       'preview_url': 'http://test.localhost:5189/'}
            result = subprocess.run([sys.executable, str(ADAPTER)], input=json.dumps(request), text=True,
                                    capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        args, prompt = json.loads(result.stdout)['findings']
        args = json.loads(args)
        self.assertIn('mcp_servers.friday.required=true', args)
        self.assertIn('mcp_servers.friday.tools.emulate.approval_mode="approve"', args)
        self.assertEqual({arg.split('.')[3] for arg in args if arg.startswith('mcp_servers.friday.tools.')}, set(TOOLS))
        self.assertIn('friday/take_snapshot', prompt)
        self.assertIn('friday/emulate', prompt)
        self.assertIn('wait for loading and redirects to settle', prompt)
        self.assertIn('check required sizes on the current page', prompt)
        self.assertIn('Before the next full navigation, restore the original viewport', prompt)
        self.assertIn('calling friday/emulate without viewport', prompt)
        self.assertIn('Report any 401', prompt)
        self.assertIn('check its expected closing path using the real button or toggle', prompt)
        self.assertIn('assess the page content with the menu closed when possible', prompt)
        self.assertIn('Report any actual failure to close it; do not assume the outcome', prompt)
        self.assertIn('Distinguish earlier network requests from the current state', prompt)
        self.assertIn('repeat the relevant check', prompt)
        self.assertIn('including any persistent failure, without assuming success or bypassing authorization', prompt)

    def test_review_schema_constrains_assigned_criteria_and_prompt_requires_verbatim(self):
        criteria = ['Поля доступны только для чтения', 'Keep "name" unchanged']
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / 'codex'
            fake.write_text("#!/usr/bin/env python3\nimport json,sys\n"
                            "if sys.argv[1:4] == ['mcp','list','--json']:\n print('[]'); sys.exit(0)\n"
                            "schema=json.load(open(sys.argv[sys.argv.index('--output-schema')+1]))\n"
                            "prompt=sys.stdin.read()\n"
                            "p=sys.argv[sys.argv.index('--output-last-message')+1]\n"
                            "open(p,'w').write(json.dumps({'verdict':'fail','findings':[json.dumps(schema),prompt],'evidence':[],'criteria_results':[]}))\n")
            fake.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ.get('PATH', ''))
            for role in ('review', 'blind-code'):
                with self.subTest(role=role):
                    request = {'role': role, 'model': 'review-model', 'criteria': criteria}
                    result = subprocess.run([sys.executable, str(ADAPTER)], input=json.dumps(request), text=True,
                                            capture_output=True, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    schema_text, prompt = json.loads(result.stdout)['findings']
                    schema = json.loads(schema_text)
                    criterion = schema['properties']['criteria_results']['items']['properties']['criterion']
                    self.assertEqual(criterion['enum'], criteria)
                    self.assertIn('copy each assigned criterion verbatim', prompt.lower())

    def test_blind_review_preserves_unverifiable_criterion_without_findings(self):
        response = {"verdict": "unverifiable", "findings": [], "evidence": [],
                    "criteria_results": [
                        {"criterion": "Fields are readonly", "status": "unverifiable",
                         "evidence": ["The diff alone cannot establish the rendered DOM behavior."]}]}
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "codex"
            fake.write_text("#!/usr/bin/env python3\nimport sys\n"
                            "if sys.argv[1:4] == ['mcp','list','--json']:\n print('[]'); sys.exit(0)\n"
                            "p=sys.argv[sys.argv.index('--output-last-message')+1]\n"
                            f"open(p,'w').write({json.dumps(json.dumps(response))})\n")
            fake.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ.get("PATH", ""))
            request = {"role": "blind-code", "model": "review-model", "criteria": ["Fields are readonly"]}
            result = subprocess.run([sys.executable, str(ADAPTER)], input=json.dumps(request), text=True,
                                    capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(result.stdout)
        self.assertEqual(actual, response)
        self.assertEqual(schemas.validate_review(actual, strict=True, criteria=request["criteria"]), "unverifiable")

    def test_review_contract_rejects_contradictions_and_missing_observation(self):
        for value in ({"verdict": "pass", "findings": ["issue"], "evidence": ["seen"]},
                      {"verdict": "fail", "findings": [], "evidence": ["seen"]},
                      {"verdict": "pass", "findings": [], "evidence": []}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                schemas.validate_review(value, strict=True, observed=True)
        self.assertEqual(schemas.validate_review({"verdict": "pass"}), "pass")
        self.assertEqual(schemas.validate_review({'verdict': 'unverifiable', 'findings': ['missing context'], 'evidence': []}, strict=True), 'unverifiable')
        response = {'verdict': 'unverifiable', 'findings': ['missing'], 'evidence': [],
                    'criteria_results': [{'criterion': 'A', 'status': 'pass', 'evidence': ['seen']},
                                         {'criterion': 'B', 'status': 'unverifiable', 'evidence': []}]}
        self.assertEqual(schemas.validate_review(response, strict=True, criteria=['A', 'B']), 'unverifiable')
        with self.assertRaises(ValueError):
            schemas.validate_review(response, strict=True, criteria=['A', 'C'])

    def test_review_uses_read_only_and_forwards_model_and_brief_via_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "codex"
            fake.write_text("#!/usr/bin/env python3\nimport json,sys\n"
                            "if sys.argv[1:4] == ['mcp','list','--json']:\n print('[]'); sys.exit(0)\n"
                            "p=sys.argv[sys.argv.index('--output-last-message')+1]\n"
                            "open(p,'w').write(json.dumps({'verdict':'fail','evidence':['test'],'findings':[json.dumps(sys.argv),sys.stdin.read()]}))\n")
            fake.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ.get("PATH", ""))
            request = {"role": "review", "model": "review-model", "criteria": ["works"], "context_only": True,
                       "engineering_policy": ["Use the project precedent."]}
            result = subprocess.run([sys.executable, str(ADAPTER)], input=json.dumps(request), text=True,
                                    capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            response = json.loads(result.stdout)
            args, prompt = response["findings"]
            args = json.loads(args)
            self.assertIn('permissions.friday.extends=":read-only"', args)
            policy = next(arg for arg in args if arg.startswith("permissions.friday.filesystem="))
            self.assertIn('\":root\"="deny"', policy)
            self.assertIn('\":workspace_roots\"={"."="read"', policy)
            self.assertEqual(args[args.index("--model") + 1], "review-model")
            self.assertIn("--ignore-user-config", args)
            self.assertIn("permissions.friday.network.enabled=false", args)
            self.assertIn('web_search="disabled"', args)
            self.assertIn("shell_tool", args)
            self.assertIn("works", prompt)
            self.assertIn("Only inspect files within the supplied worktree", prompt)
            self.assertIn("Use the project precedent.", prompt)
            self.assertIn("Apply the supplied engineering_policy", prompt)
            self.assertIn('behavior_evidence contains observations by a separate browser reviewer', prompt)
            self.assertIn('if they are insufficient, return unverifiable', prompt)
            self.assertIn('Do not claim you personally observed the browser', prompt)
            self.assertNotIn("works", args)


if __name__ == "__main__":
    unittest.main()
