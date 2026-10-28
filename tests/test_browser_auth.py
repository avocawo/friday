import json
import unittest
from unittest.mock import patch

from friday import browser_auth
from friday.errors import FridayError


class BrowserAuthTests(unittest.TestCase):
    def setUp(self):
        self.spec = {"stage_origin": "https://gql.stage.test", "auth": {"storage_key": "session", "api_path": "/gql", "source_port": 5189,
                                                                             "query": "query { viewer { id } }", "data_path": ["viewer", "id"]}}

    def test_uses_only_local_pages_and_returns_verified_session(self):
        class Client:
            def __init__(self, _):
                self.pages = []
            def tool(self, name, arguments):
                if name == "list_pages":
                    text = "1: prod (https://prod.test/)\n2: local (http://work.localhost:5189/)"
                else:
                    self.pages.append(arguments["pageId"])
                    self.source = arguments["function"]
                    text = "```json\n" + json.dumps("synthetic-token") + "\n```"
                return {"content": [{"type": "text", "text": text}]}
            def close(self):
                pass
        clients = []
        def make_client(command):
            client = Client(command)
            clients.append(client)
            return client
        with patch("friday.browser_auth.browser_probe.server_path", return_value="server"), \
             patch("friday.browser_auth.browser_protocol.Client", side_effect=make_client):
            self.assertEqual(browser_auth.capture(self.spec), "synthetic-token")
        self.assertEqual(clients[0].pages, [2])
        self.assertIn("https://gql.stage.test", clients[0].source)
        self.assertNotIn("synthetic-token", clients[0].source)

    def test_missing_verified_session_fails_without_exposing_token(self):
        class Client:
            def __init__(self, _):
                pass
            def tool(self, name, arguments):
                text = "1: http://work.localhost:5189/" if name == "list_pages" else "```json\nnull\n```"
                return {"content": [{"type": "text", "text": text}]}
            def close(self):
                pass
        with patch("friday.browser_auth.browser_probe.server_path", return_value="server"), \
             patch("friday.browser_auth.browser_protocol.Client", Client):
            with self.assertRaises(FridayError) as error:
                browser_auth.capture(self.spec)
        self.assertEqual(error.exception.code, "stage_auth_unavailable")

    def test_profiles_without_auth_remain_optional(self):
        self.assertIsNone(browser_auth.capture({"stage_origin": "https://gql.stage.test"}))

    def test_fingerprint_probe_uses_configured_local_module(self):
        self.spec["auth"]["fingerprint"] = {"module": "/src/fingerprint.ts", "export": "getFingerprint", "header": "X-Fingerprint"}
        script = browser_auth.probe_script(self.spec, source_check=True)
        self.assertIn('import("/src/fingerprint.ts")', script)
        self.assertIn('headers["X-Fingerprint"]', script)
        self.spec["auth"]["fingerprint"]["module"] = "https://production.example/script.js"
        with patch("friday.browser_auth.browser_protocol.Client") as client:
            with self.assertRaises(FridayError):
                browser_auth.capture(self.spec)
        client.assert_not_called()

    def test_rejects_mutation_before_launching_browser(self):
        queries = ("mutation { deleteAccount }", "query { viewer { id } } mutation { deleteAccount }")
        for query in queries:
            with self.subTest(query=query), patch("friday.browser_auth.browser_protocol.Client") as client:
                self.spec["auth"]["query"] = query
                with self.assertRaises(FridayError) as error:
                    browser_auth.capture(self.spec)
                self.assertEqual(error.exception.code, "invalid_profile")
                client.assert_not_called()
