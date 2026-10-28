import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters import tracker


class TrackerAdapterTests(unittest.TestCase):
    def test_create_request_keeps_reviewed_fields(self):
        payload = {"queue": "TEST", "summary": "A task", "description": "Acceptance criteria", "customField": "value"}
        self.assertEqual(tracker.arguments_for(json.dumps(payload)),
                         {"queue": "TEST", "summary": "A task", "description": "Acceptance criteria", "fields": {"customField": "value"}})

    def test_create_rejects_missing_queue_or_ambiguous_fields(self):
        for draft in ('{"summary":"A task"}', '{"queue":"TEST","summary":"A task","fields":{"queue":"OTHER"}}'):
            with self.assertRaises(ValueError):
                tracker.arguments_for(draft)

    def test_mcp_receives_reviewed_arguments_and_private_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            token, org = Path(directory) / "token", Path(directory) / "org"
            token.write_text("synthetic-token"); token.chmod(0o600)
            org.write_text("synthetic-org"); org.chmod(0o600)
            args = ["tracker.py", "--mcp-command", '["fake-mcp"]', "--token-file", str(token), "--org-file", str(org)]
            with patch("sys.argv", args), patch("sys.stdin", io.StringIO('{"queue":"TEST","summary":"A task"}')), \
                 patch("adapters.tracker.browser_protocol.Client") as client, contextlib.redirect_stdout(io.StringIO()):
                client.return_value.tool.return_value = {"structuredContent": {"key": "TEST-1"}}
                tracker.main()
            self.assertEqual(client.call_args.kwargs["env"]["TRACKER_READ_ONLY"], "false")
            self.assertEqual(client.call_args.kwargs["env"]["TRACKER_TOKEN"], "synthetic-token")
            client.return_value.tool.assert_called_once_with("issue_create", {"queue": "TEST", "summary": "A task"}, timeout=240)
            client.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
