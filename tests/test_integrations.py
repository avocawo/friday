import json
import subprocess
import unittest
from unittest.mock import patch

from friday import integrations, intake, watch
from friday.errors import FridayError


class IntegrationTests(unittest.TestCase):
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
             patch("friday.integrations.browser_protocol.Client", Client):
            issue = intake.ticket(config, "T-1")
        self.assertEqual([item["id"] for item in issue["comments"]], ["first", "next"])
        self.assertEqual(clients[0].env["TRACKER_READ_ONLY"], "true")
        self.assertTrue(clients[0].closed)
        self.assertEqual({name for name, _ in clients[0].calls},
                         {"issue_get", "issue_get_comments", "issue_get_links", "issue_get_attachments"})

    def test_gitlab_uses_get_with_cli_and_project_from_profile(self):
        config = {"backend": "glab", "base_url": "https://gitlab.example", "project": "team/repo"}
        data = {"iid": 12, "state": "opened", "head_pipeline": {"status": "success"}, "user_notes_count": 2}
        with patch("friday.integrations.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(data), "")) as run:
            observation = watch.fetch(config, 12)
        self.assertEqual(observation["pipeline"], "success")
        self.assertEqual(run.call_args.args[0], ["glab", "api", "--hostname", "gitlab.example", "--method", "GET",
                                                 "projects/team%2Frepo/merge_requests/12"])

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
             patch("friday.integrations.browser_protocol.Client", Client):
            with self.assertRaises(FridayError) as error:
                intake.ticket({"tracker": {"backend": "mcp", "mcp_command": ["fake"], "org_id": "org"}}, "T-1")
        self.assertEqual(error.exception.code, "tracker_error")
        self.assertTrue(clients[0].closed)

    def test_gitlab_cli_failure_does_not_report_success(self):
        with patch("friday.integrations.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "private")):
            with self.assertRaises(FridayError) as error:
                integrations.gitlab_get({"base_url": "https://gitlab.example"}, "projects/a")
        self.assertEqual(error.exception.code, "gitlab_unavailable")
        self.assertNotIn("private", str(error.exception))


if __name__ == "__main__":
    unittest.main()
