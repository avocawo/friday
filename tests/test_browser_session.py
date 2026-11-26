import json
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
                                ('take_snapshot', {'filePath': '/tmp/output'}),
                                ('emulate', {'viewport': '375x900x1', 'extraHttpHeaders': '{}'}),
                                ('emulate', {'pageId': 1, 'viewport': '375x900x1'})]:
            with self.subTest(name=name, arguments=arguments), self.assertRaises(FridayError):
                session.call(name, arguments)
        session.client.tool.assert_not_called()
        session.call('take_snapshot', {})
        session.client.tool.assert_called_once_with('take_snapshot', {'pageId': 7})
        session.call('emulate', {'viewport': '375x900x1'})
        session.client.tool.assert_called_with('emulate', {'pageId': 7, 'viewport': '375x900x1'})

    def test_computed_style_runs_a_fixed_script_on_one_element(self):
        session = browser_session.Session.__new__(browser_session.Session)
        session.client = Mock()
        session.page = 7
        session.call('computed_style', {'uid': '1_23', 'properties': ['border-radius', 'height']})
        name, arguments = session.client.tool.call_args.args
        self.assertEqual((name, arguments['pageId'], arguments['args']), ('evaluate_script', 7, ['1_23']))
        self.assertIn('["border-radius", "height"]', arguments['function'])
        self.assertIn('getComputedStyle(element)', arguments['function'])
        session.client.reset_mock()
        for arguments in ({'uid': '1_23', 'properties': ['x); fetch("//evil")']}, {'uid': 'a b', 'properties': []},
                          {'uid': '1', 'properties': ['color'], 'function': '()=>1'}, {'uid': '1', 'properties': 'color'}):
            with self.subTest(arguments=arguments), self.assertRaises(FridayError):
                session.call('computed_style', arguments)
        session.client.tool.assert_not_called()
        session.call('hover', {'uid': '1_23'})
        session.client.tool.assert_called_once_with('hover', {'pageId': 7, 'uid': '1_23'})
        session.client.request.return_value = {'tools': [{'name': 'hover', 'inputSchema': {'properties': {'uid': {}, 'pageId': {}}}},
                                                         {'name': 'evaluate_script', 'inputSchema': {'properties': {}}}]}
        self.assertEqual([tool['name'] for tool in session.tools()], ['hover', 'computed_style', 'element_attributes'])

    def test_element_attributes_reads_only_listed_markup_attributes_with_a_fixed_script(self):
        session = browser_session.Session.__new__(browser_session.Session)
        session.client = Mock()
        session.page = 7
        session.call('element_attributes', {'uid': '1_23', 'names': ['aria-current', 'data-link-component', 'href']})
        name, arguments = session.client.tool.call_args.args
        self.assertEqual((name, arguments['pageId'], arguments['args']), ('evaluate_script', 7, ['1_23']))
        self.assertIn('["aria-current", "data-link-component", "href"]', arguments['function'])
        self.assertIn('getAttribute(name)', arguments['function'])
        session.client.reset_mock()
        for arguments in ({'uid': '1', 'names': ['value']}, {'uid': '1', 'names': ['onclick']}, {'uid': '1', 'names': ['src']},
                          {'uid': '1', 'names': ['aria-x"); fetch("//evil']}, {'uid': 'a b', 'names': []},
                          {'uid': '1', 'names': ['role'], 'function': '()=>1'}, {'uid': '1', 'names': 'role'}):
            with self.subTest(arguments=arguments), self.assertRaises(FridayError):
                session.call('element_attributes', arguments)
        session.client.tool.assert_not_called()

    def test_console_is_listed_for_the_preview_tab_only_in_local_mode(self):
        upstream = {'tools': [{'name': 'list_console_messages', 'inputSchema': {'properties': {'types': {}, 'pageId': {}, 'serviceWorkerId': {}}}},
                              {'name': 'get_console_message', 'inputSchema': {'properties': {}}}]}
        local = browser_session.Session.__new__(browser_session.Session)
        local.client, local.page, local.stage = Mock(), 7, None
        local.client.request.return_value = json.loads(json.dumps(upstream))
        tools = {tool['name']: tool for tool in local.tools()}
        self.assertNotIn('get_console_message', tools)
        self.assertEqual(set(tools['list_console_messages']['inputSchema']['properties']), {'types'})
        local.call('list_console_messages', {'types': ['error']})
        local.client.tool.assert_called_once_with('list_console_messages', {'pageId': 7, 'types': ['error']})
        for arguments in ({'serviceWorkerId': 'sw-1'}, {'pageId': 3}):
            with self.subTest(arguments=arguments), self.assertRaises(FridayError):
                local.call('list_console_messages', arguments)
        staging = browser_session.Session.__new__(browser_session.Session)
        staging.client, staging.page, staging.stage = Mock(), 7, 'https://app.stage.example.com'
        staging.client.request.return_value = json.loads(json.dumps(upstream))
        self.assertNotIn('list_console_messages', [tool['name'] for tool in staging.tools()])
        with self.assertRaises(FridayError):
            staging.call('list_console_messages', {})
        staging.client.tool.assert_not_called()
        self.assertIn('list_console_messages', browser_session.agent_tools({'mode': 'local'}))
        self.assertNotIn('list_console_messages', browser_session.agent_tools({'mode': 'staging'}))
        self.assertIn('element_attributes', browser_session.agent_tools({'mode': 'staging'}))

    def test_emulation_is_cleared_before_navigation_and_history_stays_on_the_preview(self):
        session = browser_session.Session.__new__(browser_session.Session)
        session.origin = 'http://test.localhost:5189'
        session.page = 7
        session.client = Mock()
        session.client.tool.return_value = {'content': [{'type': 'text', 'text': '7: http://test.localhost:5189/list [selected]'}]}
        session.call('emulate', {'viewport': '375x900x1'})
        session.call('navigate_page', {'type': 'reload'})
        calls = [call.args for call in session.client.tool.call_args_list]
        self.assertEqual(calls[1:3], [('emulate', {'pageId': 7}), ('navigate_page', {'type': 'reload', 'pageId': 7})])
        session.client.tool.reset_mock()
        session.call('navigate_page', {'type': 'back'})
        self.assertEqual([call.args[0] for call in session.client.tool.call_args_list], ['navigate_page', 'list_pages'])
        session.client.tool.return_value = {'content': [{'type': 'text', 'text': '7: about:blank'}]}
        with self.assertRaises(FridayError):
            session.call('navigate_page', {'type': 'back'})
        session.client.tool.assert_called_with('navigate_page', {'pageId': 7, 'type': 'url', 'url': 'http://test.localhost:5189/'})


class StagingOriginTests(unittest.TestCase):
    def test_only_an_explicit_origin_named_as_a_test_environment_is_accepted(self):
        from friday import browser_probe
        for origin in ("https://gql.stage.example", "https://api.staging.example.com", "https://app-dev.example.com",
                       "https://qa.example.org:443"):
            self.assertTrue(browser_session.stage_origin({"stage_origin": origin}).startswith("https://"), origin)
        for origin in ("https://api.example.com", "http://api.stage.example", "https://api.stage.example/path",
                       "https://user@api.stage.example", "https://api.stage.example:8443", "https://*.stage.example"):
            with self.assertRaises(FridayError, msg=origin):
                browser_session.stage_origin({"stage_origin": origin})
        self.assertEqual(browser_session.stage_origin({"stage_origin": "https://api.int.example", "stage_host_pattern": r".*\.int\.example"}),
                         "https://api.int.example")
        self.assertEqual(browser_probe.chrome_url({"chrome_url": "http://127.0.0.1:9222"}), "http://127.0.0.1:9222")
        with self.assertRaises(FridayError):
            browser_probe.chrome_url({"chrome_url": "http://example.com:9222"})
        with self.assertRaises(FridayError):
            browser_session.stage_check({"stage_check": {"path": "https://x/main.ts", "variable": "API"}})
