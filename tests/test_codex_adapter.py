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
    def test_review_contract_rejects_contradictions_and_missing_observation(self):
        for value in ({"verdict": "pass", "findings": ["issue"], "evidence": ["seen"]},
                      {"verdict": "fail", "findings": [], "evidence": ["seen"]},
                      {"verdict": "pass", "findings": [], "evidence": []}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                schemas.validate_review(value, strict=True, observed=True)
        self.assertEqual(schemas.validate_review({"verdict": "pass"}), "pass")

    def test_review_uses_read_only_and_forwards_model_and_brief_via_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "codex"
            fake.write_text("#!/usr/bin/env python3\nimport json,sys\n"
                            "if sys.argv[1:4] == ['mcp','list','--json']:\n print('[]'); sys.exit(0)\n"
                            "p=sys.argv[sys.argv.index('--output-last-message')+1]\n"
                            "open(p,'w').write(json.dumps({'verdict':'fail','evidence':['test'],'findings':[sys.argv,sys.stdin.read()]}))\n")
            fake.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ.get("PATH", ""))
            request = {"role": "review", "model": "review-model", "criteria": ["works"], "context_only": True,
                       "engineering_policy": ["Use the project precedent."]}
            result = subprocess.run([sys.executable, str(ADAPTER)], input=json.dumps(request), text=True,
                                    capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            response = json.loads(result.stdout)
            args, prompt = response["findings"]
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
            self.assertNotIn("works", args)


if __name__ == "__main__":
    unittest.main()
