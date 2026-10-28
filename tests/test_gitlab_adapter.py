import argparse
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters import gitlab


class GitLabAdapterTests(unittest.TestCase):
    def test_merge_is_blocked_without_atomic_target_guard(self):
        args = argparse.Namespace(base="https://gitlab.example", project="team/project", action="merge", source=None, target=None)
        with self.assertRaises(ValueError):
            gitlab.request_for(args, "approved")

    def test_create_mr_uses_cli_and_exact_reviewed_text(self):
        args = argparse.Namespace(base="https://gitlab.example", project="team/project", action="create-mr", source="feature", target="main")
        command, payload = gitlab.request_for(args, json.dumps({"title": "Title", "description": "Body"}))
        self.assertEqual(payload, {"title": "Draft: Title", "description": "Body", "source_branch": "feature", "target_branch": "main"})
        self.assertEqual(command, ["glab", "api", "--hostname", "gitlab.example", "--method", "POST", "projects/team%2Fproject/merge_requests", "--input", "-"])

    def test_adapter_passes_token_only_in_environment_and_payload_on_stdin(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"OAUTH_TOKEN": "other"}, clear=True):
            token = Path(directory) / "token"
            token.write_text("synthetic-token")
            token.chmod(0o600)
            args = ["gitlab.py", "create-mr", "--base", "https://gitlab.example", "--project", "team/project",
                    "--source", "feature", "--target", "main", "--token-file", str(token)]
            response = subprocess.CompletedProcess([], 0, '{"iid":1}', '')
            with patch("sys.argv", args), patch("sys.stdin", io.StringIO('{"title":"T","description":"D"}')), \
                 patch("adapters.gitlab.subprocess.run", return_value=response) as run, contextlib.redirect_stdout(io.StringIO()):
                gitlab.main()
            self.assertEqual(run.call_args.kwargs["env"]["GITLAB_TOKEN"], "synthetic-token")
            self.assertNotIn("OAUTH_TOKEN", run.call_args.kwargs["env"])
            self.assertNotIn("synthetic-token", repr(run.call_args.args))
            self.assertEqual(json.loads(run.call_args.kwargs["input"])["description"], "D")


if __name__ == "__main__":
    unittest.main()
