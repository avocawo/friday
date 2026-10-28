import unittest
from unittest.mock import Mock, patch

from friday import browser_probe, browser_session
from friday.errors import FridayError


class BrowserSessionTests(unittest.TestCase):
    def test_verified_token_is_seeded_only_in_isolated_preview_and_removed(self):
        spec = {"stage_origin": "https://gql.stage.test", "auth": {"storage_key": "session", "api_path": "/gql", "source_port": 5189,
                                                                       "query": "query { viewer { id } }", "data_path": ["viewer", "id"]}}
        fixture = Mock(origin="http://127.0.0.1:9000", forbidden_origin="http://127.0.0.1:9001")
        client = Mock()
        def tool(name, arguments, **kwargs):
            if name == "list_pages":
                value = "1: http://127.0.0.1:9000/"
            elif name == "new_page":
                self.assertIn("isolatedContext", arguments)
                value = "2: http://test.localhost:5189/"
            else:
                value = "```json\ntrue\n```"
            return {"content": [{"type": "text", "text": value}]}
        client.tool.side_effect = tool
        with patch("friday.browser_session.browser_probe.server_path", return_value="server"), \
             patch("friday.browser_session.browser_probe.Fixture", return_value=fixture), \
             patch("friday.browser_session.browser_probe.exercise", return_value=1), \
             patch("friday.browser_session.browser_probe.cleanup"), \
             patch("friday.browser_session.browser_auth.capture", return_value="synthetic-token"), \
             patch("friday.browser_session.browser_protocol.Client", return_value=client):
            session = browser_session.Session(spec, "http://test.localhost:5189/")
            session.close()
        calls = [call.args for call in client.tool.call_args_list if call.args[0] == "evaluate_script"]
        self.assertTrue(any("localStorage.setItem" in args[1]["function"] and "synthetic-token" in args[1]["function"] for args in calls))
        self.assertTrue(any("localStorage.removeItem" in args[1]["function"] for args in calls))

    def test_page_listing_supports_titles_without_confusing_origins(self):
        listing = '1: Page title (http://one.localhost:5189/path)\n2: http://two.localhost:9000/ [selected]'
        self.assertEqual(browser_probe.pages(listing), [(1, 'http://one.localhost:5189/path'), (2, 'http://two.localhost:9000/')])
        self.assertFalse(browser_probe.same_origin('http://one.localhost:51890/', 'http://one.localhost:5189'))

    def test_non_staging_and_wildcard_destinations_are_rejected(self):
        for preview, stage in [('http://*.localhost:5189/', 'https://gql.stage.example'),
                               ('http://local.localhost:5189/', 'https://gql.example'),
                               ('http://local.localhost:5189/', 'https://*.stage.example')]:
            with self.subTest(preview=preview, stage=stage), self.assertRaises(FridayError):
                browser_session.configuration({'stage_origin': stage}, preview)

    def test_arbitrary_script_page_id_and_external_navigation_are_rejected(self):
        session = browser_session.Session.__new__(browser_session.Session)
        session.origin = 'http://test.localhost:5189'
        session.client = Mock()
        session.page = 7
        for name, arguments in [('evaluate_script', {'function': '()=>1'}),
                                ('take_snapshot', {'pageId': 1}),
                                ('navigate_page', {'url': 'https://prod.example/'}),
                                ('navigate_page', {'url': 'http://test.localhost:5189/', 'initScript': 'alert(1)'}),
                                ('take_snapshot', {'filePath': '/tmp/output'})]:
            with self.subTest(name=name, arguments=arguments), self.assertRaises(FridayError):
                session.call(name, arguments)
        session.client.tool.assert_not_called()
        session.call('take_snapshot', {})
        session.client.tool.assert_called_once_with('take_snapshot', {'pageId': 7})
