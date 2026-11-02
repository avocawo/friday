import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import tracker
from friday.errors import FridayError


class TrackerTests(unittest.TestCase):
    def test_tracker_requires_credentials_without_sending_request(self):
        config = {"tracker": {"base_url": "https://example.invalid", "token_env": "NEVER_CONFIGURED_TOKEN",
                              "org_env": "NEVER_CONFIGURED_ORG"}}
        with self.assertRaises(FridayError) as error:
            tracker.ticket(config, "ABC-1")
        self.assertEqual(error.exception.code, "credentials_missing")

    def test_tracker_reads_v3_issue_and_related_context(self):
        config = {"tracker": {"base_url": "https://tracker.example", "token_env": "TOKEN_FOR_TEST",
                              "org_env": "ORG_FOR_TEST"}}
        response = {"key": "ABC-1", "summary": "Issue", "comments": [{"text": "comment"}]}
        with patch.dict("os.environ", {"TOKEN_FOR_TEST": "secret", "ORG_FOR_TEST": "123"}), \
             patch("friday.tracker.http.read_json", return_value=response) as get:
            issue = tracker.ticket(config, "ABC-1")
        request = get.call_args.args[0]
        self.assertEqual(request.full_url, "https://tracker.example/v3/issues/ABC-1?expand=comments,links,attachments")
        self.assertEqual(request.get_header("Authorization"), "OAuth secret")
        self.assertEqual(issue["comments"], [{"text": "comment"}])

    def test_tracker_reads_credentials_from_private_files(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            token, organization = Path(directory) / "token", Path(directory) / "org"
            token.write_text("synthetic-token")
            organization.write_text("123")
            token.chmod(0o600)
            organization.chmod(0o600)
            config = {"tracker": {"base_url": "https://tracker.example", "token_file": str(token), "org_file": str(organization)}}
            with patch("friday.tracker.http.read_json", return_value={"key": "ABC-1"}) as get:
                tracker.ticket(config, "ABC-1")
            self.assertEqual(get.call_args.args[0].get_header("X-org-id"), "123")
            self.assertEqual(get.call_args.args[0].get_header("Authorization"), "OAuth synthetic-token")

    def test_tracker_mcp_collects_all_comment_pages_in_read_only_mode(self):
        class Client:
            def __init__(self, command, env):
                self.calls = []
                self.env = env
                self.closed = False
                clients.append(self)

            def tool(self, name, args, timeout):
                self.calls.append((name, args))
                if name == "issue_get":
                    data = {"key": "T-1", "summary": "Issue"}
                elif name == "issue_get_comments":
                    data = {"comments": [{"id": args.get("cursor", "first")}],
                            "next_cursor": None if args.get("cursor") else "next"}
                else:
                    data = {"result": []}
                return {"structuredContent": data}

            def close(self):
                self.closed = True

        clients = []
        config = {"tracker": {"backend": "mcp", "mcp_command": ["fake-mcp"], "org_id": "org"}}
        with patch.dict("os.environ", {"FRIDAY_TRACKER_TOKEN": "secret"}), \
             patch("friday.tracker.browser_protocol.Client", Client):
            issue = tracker.ticket(config, "T-1")
        self.assertEqual([item["id"] for item in issue["comments"]], ["first", "next"])
        self.assertEqual(clients[0].env["TRACKER_READ_ONLY"], "true")
        self.assertTrue(clients[0].closed)
        self.assertEqual({name for name, _ in clients[0].calls},
                         {"issue_get", "issue_get_comments", "issue_get_links", "issue_get_attachments"})

    def test_tracker_mcp_rejects_repeating_cursor_and_closes(self):
        class Client:
            def __init__(self, command, env):
                clients.append(self)
                self.closed = False

            def tool(self, name, args, timeout):
                data = {"key": "T-1"} if name == "issue_get" else {"comments": [], "next_cursor": "same"}
                return {"structuredContent": data}

            def close(self):
                self.closed = True

        clients = []
        with patch.dict("os.environ", {"FRIDAY_TRACKER_TOKEN": "secret"}), \
             patch("friday.tracker.browser_protocol.Client", Client):
            with self.assertRaises(FridayError) as error:
                tracker.ticket({"tracker": {"backend": "mcp", "mcp_command": ["fake"], "org_id": "org"}}, "T-1")
        self.assertEqual(error.exception.code, "tracker_error")
        self.assertTrue(clients[0].closed)


if __name__ == "__main__":
    unittest.main()
