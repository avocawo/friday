import contextlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import stages, state, workflow
from friday.errors import FridayError


class WorkflowTests(unittest.TestCase):
    def test_browser_test_context_is_strictly_bounded(self):
        context = {'mode': 'local_mock', 'route': '/dashboards/monitored-client', 'query_key': 'monitoredClientMock', 'scenarios': ['card-error']}
        self.assertEqual(workflow.browser_test_context(context), context)
        for changes in ({'route': 'https://example.test'}, {'route': '//example.test'}, {'query_key': 'accessToken'},
                        {'scenarios': ['arbitrary text']}, {'scenarios': ['card-error', 'card-error']}, {'text': 'arbitrary'}, {'mode': 'other'}):
            with self.subTest(changes=changes), self.assertRaises(FridayError):
                workflow.browser_test_context(dict(context, **changes))

    def test_manual_browser_context_is_forwarded_and_recorded(self):
        self.write_plan([{'id': 'one', 'brief': 'update', 'criteria': ['works']}])
        workflow.load_plan(self.root, 'T-1', self.plan)
        context = {'mode': 'local_mock', 'route': '/dashboards/monitored-client', 'query_key': 'monitoredClientMock', 'scenarios': ['card-error', 'client-removed']}
        event = workflow.invoke(self.root, 'T-1', 'one', 'blind-behavior', browser_context=context)
        seen = json.loads(Path(event['evidence']).read_text())['seen']
        self.assertEqual(event['browser_test_context'], context)
        self.assertEqual(seen['browser_test_context']['scenario_paths'], [
            '/dashboards/monitored-client?monitoredClientMock=card-error',
            '/dashboards/monitored-client?monitoredClientMock=client-removed'])
        self.assertEqual(seen['criteria'], ['works'])
        with self.assertRaises(FridayError):
            workflow.invoke(self.root, 'T-1', 'one', 'review', browser_context=context)

    def setUp(self):
        preview_mock = patch('friday.isolated_preview.open_preview', side_effect=lambda *_: contextlib.nullcontext('http://test.localhost:1234/'))
        preview_mock.start()
        self.addCleanup(preview_mock.stop)
        for name in ("require_runner", "require_browser"):
            mock = patch("friday.isolation." + name)
            mock.start()
            self.addCleanup(mock.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        def git(*args):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        self.git = git
        git("init", "-q")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        (self.repo / "file").write_text("code")
        git("add", "file")
        git("commit", "-qm", "initial")
        echo = ["python3", "-c", "import json,sys; print(json.dumps({'verdict': 'pass', 'evidence': ['observed'], 'seen': json.load(sys.stdin)}))"]
        runners = {"develop": {"model": "a", "command": echo},
                   "blind-behavior": {"model": "b", "command": echo}}
        self.config = self.root / "profile.json"
        self.config.write_text(json.dumps({"name": "test", "repository": str(self.repo), "runners": runners}))
        state.create(self.root, "T-1", str(self.config))
        self.plan = self.root / "plan.json"

    def write_plan(self, items):
        self.plan.write_text(json.dumps({"stages": items}))

    def test_stage_steps_are_saved_and_given_to_the_developer(self):
        self.write_plan([{'id': 'one', 'brief': 'update', 'steps': ['Inspect precedent', 'Apply it'], 'criteria': ['works']}])
        workflow.load_plan(self.root, 'T-1', self.plan)
        workflow.approve_plan(self.root, 'T-1')
        result = workflow.invoke(self.root, 'T-1', 'one', 'develop')
        request = json.loads(Path(result['evidence']).read_text())['seen']
        self.assertEqual(request['steps'], ['Inspect precedent', 'Apply it'])

    def test_invalid_stage_steps_are_rejected(self):
        self.write_plan([{'id': 'one', 'brief': 'update', 'steps': [''], 'criteria': ['works']}])
        with self.assertRaises(FridayError) as error:
            workflow.load_plan(self.root, 'T-1', self.plan)
        self.assertEqual(error.exception.code, 'invalid_plan')

    def test_dependencies_and_blind_behavior_brief(self):
        self.write_plan([{"id": "one", "brief": "secret implementation", "criteria": ["works"]},
                         {"id": "two", "brief": "second", "criteria": ["works"], "depends_on": ["one"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        self.assertEqual(workflow.ready(state.read(self.root, "T-1")), ["one"])
        result = workflow.invoke(self.root, "T-1", "one", "blind-behavior")
        request = json.loads(Path(result["evidence"]).read_text())["seen"]
        self.assertEqual(request, {"role": "blind-behavior", "criteria": ["works"], "model": "b", "preview_url": "http://test.localhost:1234/"})
        stages.close(self.root, "T-1", "one", "owner", "accepted")
        self.assertEqual(workflow.ready(state.read(self.root, "T-1")), ["two"])

    def test_review_receives_only_criteria_for_its_kind(self):
        config = json.loads(self.config.read_text())
        capture = "import json,sys; r=json.load(sys.stdin); print(json.dumps({'verdict':'pass','findings':[],'evidence':['observed'],'seen':r,'criteria_results':[{'criterion':c,'status':'pass','evidence':['observed']} for c in r['criteria']]}))"
        config['runners']['blind-behavior']['command'] = ['python3', '-c', capture]
        self.config.write_text(json.dumps(config))
        self.write_plan([{'id': 'one', 'brief': 'update', 'criteria': [
            {'text': 'type is correct', 'checks': ['code']},
            {'text': 'dialog opens', 'checks': ['behavior']},
        ]}])
        workflow.load_plan(self.root, 'T-1', self.plan)
        result = workflow.invoke(self.root, 'T-1', 'one', 'blind-behavior')
        request = json.loads(Path(result['evidence']).read_text())['seen']
        self.assertEqual(request['criteria'], ['dialog opens'])

    def test_review_gets_only_current_passing_browser_evidence_including_fallback(self):
        config = json.loads(self.config.read_text())
        capture = "import json,sys; r=json.load(sys.stdin); print(json.dumps({'verdict':'pass','findings':[],'evidence':['observed'],'seen':r,'criteria_results':[{'criterion':c,'status':'pass','evidence':['observed']} for c in r['criteria']]}))"
        config['runners']['review'] = {'model': 'review', 'command': ['python3', '-c', capture]}
        config['runners']['blind-behavior']['command'] = ['python3', '-c', capture]
        self.config.write_text(json.dumps(config))
        self.write_plan([{'id': 'one', 'brief': 'update', 'criteria': [
            {'text': 'code works', 'checks': ['code']}, {'text': 'dialog opens', 'checks': ['behavior']}]}])
        workflow.load_plan(self.root, 'T-1', self.plan)
        before = workflow.invoke(self.root, 'T-1', 'one', 'review')
        self.assertNotIn('behavior_evidence', json.loads(Path(before['evidence']).read_text())['seen'])
        browser = workflow.invoke(self.root, 'T-1', 'one', 'blind-behavior')
        card = state.read(self.root, 'T-1')
        stage = card['stages']['one']
        self.assertIsNotNone(workflow.behavior_evidence(self.root, 'T-1', card, 'one', stage, browser['head']))
        for field in ('head', 'decision_revision', 'plan_revision', 'verdict', 'evidence_sha256'):
            altered_card = json.loads(json.dumps(card))
            altered_card['events'][-1][field] = 'wrong'
            self.assertIsNone(workflow.behavior_evidence(self.root, 'T-1', altered_card, 'one', stage, browser['head']))
        altered_card = json.loads(json.dumps(card))
        altered_card['events'].append(dict(browser, verdict='fail'))
        self.assertIsNone(workflow.behavior_evidence(self.root, 'T-1', altered_card, 'one', stage, browser['head']))
        path = Path(browser['evidence'])
        original = path.read_text()
        path.write_text(original + ' ')
        altered = workflow.invoke(self.root, 'T-1', 'one', 'review')
        self.assertNotIn('behavior_evidence', json.loads(Path(altered['evidence']).read_text())['seen'])
        path.write_text(original)
        for kwargs in ({}, {'criteria_override': ['code works'], 'fallback_for': 'blind-code'}):
            review = workflow.invoke(self.root, 'T-1', 'one', 'review', **kwargs)
            seen = json.loads(Path(review['evidence']).read_text())['seen']
            self.assertEqual(seen['behavior_evidence'], [{'criterion': 'dialog opens', 'status': 'pass', 'evidence': ['observed']}])
            self.assertEqual(Path(review['behavior_evidence_binding']['evidence']), Path(browser['evidence']).resolve())

    def test_browser_evidence_requires_latest_current_complete_pass_and_redacts(self):
        self.write_plan([{'id': 'one', 'brief': 'update', 'criteria': [
            {'text': 'dialog opens', 'checks': ['behavior']}]}])
        workflow.load_plan(self.root, 'T-1', self.plan)
        config = json.loads(self.config.read_text())
        capture = "import json,sys; r=json.load(sys.stdin); print(json.dumps({'verdict':'pass','findings':[],'criterion':'Bearer abc123','evidence':['Bearer abc123'],'criteria_results':[{'criterion':c,'status':'pass','evidence':['token=abc123']} for c in r['criteria']]}))"
        config['runners']['blind-behavior']['command'] = ['python3', '-c', capture]
        self.config.write_text(json.dumps(config))
        event = workflow.invoke(self.root, 'T-1', 'one', 'blind-behavior')
        self.assertNotIn('abc123', Path(event['evidence']).read_text())
        card = state.read(self.root, 'T-1')
        stage = card['stages']['one']
        head = event['head']
        self.assertIsNone(workflow.behavior_evidence(self.root, 'T-1', card, 'one', stage, head))

        for field in ('head', 'decision_revision', 'plan_revision', 'verdict'):
            altered = json.loads(json.dumps(card))
            altered['events'][-1][field] = 'wrong'
            self.assertIsNone(workflow.behavior_evidence(self.root, 'T-1', altered, 'one', stage, head))
        altered = json.loads(json.dumps(card))
        altered['events'].append(dict(event, verdict='unverifiable'))
        self.assertIsNone(workflow.behavior_evidence(self.root, 'T-1', altered, 'one', stage, head))
        evidence = json.loads(Path(event['evidence']).read_text())
        evidence['criteria_results'] = []
        Path(event['evidence']).write_text(json.dumps(evidence))
        self.assertIsNone(workflow.behavior_evidence(self.root, 'T-1', card, 'one', stage, head))

    def test_sensitive_browser_headers_and_query_are_not_retained(self):
        for text in ('Cookie: session=abc; csrf=secret', 'Authorization: Basic dXNlcjpwYXNz',
                     'https://example.test/?access_token=secret'):
            with self.subTest(text=text):
                cleaned = workflow.redact_browser_text(text)
                for secret in ('abc', 'secret', 'dXNlcjpwYXNz'):
                    self.assertNotIn(secret, cleaned)
                self.assertIn('[REDACTED]', cleaned)

    def test_sensitive_criterion_is_rejected_without_changing_its_identity(self):
        self.write_plan([{'id': 'one', 'brief': 'update', 'criteria': ['URL token=abc works']}])
        workflow.load_plan(self.root, 'T-1', self.plan)
        config = json.loads(self.config.read_text())
        capture = "import json,sys; r=json.load(sys.stdin); print(json.dumps({'verdict':'pass','findings':[],'evidence':['observed'],'criteria_results':[{'criterion':c,'status':'pass','evidence':['observed']} for c in r['criteria']]}))"
        config['runners']['blind-behavior']['command'] = ['python3', '-c', capture]
        self.config.write_text(json.dumps(config))
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, 'T-1', 'one', 'blind-behavior')
        self.assertEqual(error.exception.code, 'invalid_verdict')

    def test_close_rejects_changed_browser_evidence_used_by_review(self):
        config = json.loads(self.config.read_text())
        capture = "import json,sys; r=json.load(sys.stdin); print(json.dumps({'verdict':'pass','findings':[],'evidence':['observed'],'criteria_results':[{'criterion':c,'status':'pass','evidence':['observed']} for c in r['criteria']]}))"
        config['runners']['review'] = {'model': 'review', 'command': ['python3', '-c', capture]}
        config['runners']['blind-behavior']['command'] = ['python3', '-c', capture]
        self.config.write_text(json.dumps(config))
        self.write_plan([{'id': 'one', 'brief': 'update', 'criteria': [{'text': 'dialog opens', 'checks': ['behavior']}]}])
        workflow.load_plan(self.root, 'T-1', self.plan)
        browser = workflow.invoke(self.root, 'T-1', 'one', 'blind-behavior')
        workflow.invoke(self.root, 'T-1', 'one', 'review')
        def set_gate(card):
            from friday import gate
            record = {'status': 'passed', 'exit_code': 0, 'unchanged': True, 'head': browser['head'], 'snapshot': 'snap',
                                             'definition': 'definition', 'plan_revision': card['plan_revision'],
                                             'decision_revision': card.get('decision_revision', 0)}
            card['stages']['one']['gate'] = record
            card['events'].append({'type': 'gate', 'stage': 'one', 'status': 'passed', 'record_sha256': gate.record_digest(record)})
        state.update(self.root, 'T-1', set_gate)
        path = Path(browser['evidence'])
        path.write_text(path.read_text() + ' ')
        with patch('friday.stages.gate.with_baseline', side_effect=lambda selected, card: selected), \
             patch('friday.stages.gate.snapshot', return_value=(browser['head'], 'snap', None)), \
             patch('friday.stages.gate.image_id', return_value='image'), \
             patch('friday.stages.gate.definition', return_value='definition'):
            with self.assertRaises(FridayError) as error:
                stages.close(self.root, 'T-1', 'one', 'gate')
        self.assertEqual(error.exception.code, 'stale_evidence')

    def test_review_receives_only_verified_current_gate_summary(self):
        from friday import gate
        config = json.loads(self.config.read_text())
        config.update(gate=['npm', 'run', 'check'], gate_image='image')
        capture = "import json,sys; r=json.load(sys.stdin); print(json.dumps({'verdict':'pass','findings':[],'evidence':['observed'],'seen':r}))"
        config['runners']['review'] = {'model': 'review', 'command': ['python3', '-c', capture]}
        self.config.write_text(json.dumps(config))
        self.write_plan([{'id': 'one', 'brief': 'update', 'criteria': ['works']}])
        workflow.load_plan(self.root, 'T-1', self.plan)
        head = subprocess.run(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
        def setup(card):
            record = {'status': 'passed', 'exit_code': 0, 'unchanged': True, 'head': head, 'snapshot': 'snap',
                      'definition': 'def', 'decision_revision': card.get('decision_revision', 0), 'plan_revision': card['plan_revision']}
            card['stages']['one']['gate'] = record
            card['events'].append({'type': 'gate', 'stage': 'one', 'status': 'passed', 'record_sha256': gate.record_digest(record)})
        state.update(self.root, 'T-1', setup)
        with patch('friday.workflow.gate.snapshot', return_value=(head, 'snap', b'')), \
             patch('friday.workflow.gate.image_id', return_value='image-id'), \
             patch('friday.workflow.gate.definition', return_value='def'):
            event = workflow.invoke(self.root, 'T-1', 'one', 'review')
            seen = json.loads(Path(event['evidence']).read_text())['seen']
            self.assertEqual(seen['gate_evidence']['command'], ['npm', 'run', 'check'])
            self.assertTrue(event['gate_evidence_binding'])
            state.update(self.root, 'T-1', lambda card: card['stages']['one']['gate'].update({'extra': 'changed'}))
            with patch('friday.stages.gate.snapshot', return_value=(head, 'snap', b'')), \
                 patch('friday.stages.gate.image_id', return_value='image-id'), \
                 patch('friday.stages.gate.definition', return_value='def'):
                with self.assertRaises(FridayError) as error:
                    stages.close(self.root, 'T-1', 'one', 'gate')
            self.assertEqual(error.exception.code, 'stale_evidence')
            state.update(self.root, 'T-1', lambda card: card['stages']['one']['gate'].pop('extra'))
            card = state.read(self.root, 'T-1')
            stage = card['stages']['one']
            for field in ('head', 'snapshot', 'definition', 'decision_revision', 'plan_revision', 'status', 'exit_code'):
                changed = json.loads(json.dumps(card))
                changed['stages']['one']['gate'][field] = 'wrong'
                self.assertIsNone(workflow.current_gate_evidence(changed, changed['stages']['one'], config, str(self.repo), head))
            card['events'] = [e for e in card['events'] if e.get('type') != 'gate']
            self.assertIsNone(workflow.current_gate_evidence(card, stage, config, str(self.repo), head))

    def test_dispute_keeps_plan_and_requires_a_real_project_rule(self):
        from friday import disputes
        (self.repo / 'rule.md').write_text('Always preserve the contract.\n')
        self.git('add', 'rule.md'); self.git('commit', '-qm', 'rule')
        self.write_plan([{'id': 'one', 'brief': 'change', 'criteria': ['works']}])
        workflow.load_plan(self.root, 'T-1', self.plan)
        before = state.read(self.root, 'T-1')['plan_revision']
        dispute = {'finding': 'wrong contract', 'rule_path': 'rule.md', 'quote': 'Always preserve the contract.',
                   'precedent': 'file', 'reason': 'The current code preserves it.'}
        disputes.record(self.root, 'T-1', 'one', dispute)
        card = state.read(self.root, 'T-1')
        self.assertEqual(card['plan_revision'], before)
        self.assertEqual(card['stages']['one']['review_disputes'][0]['quote'], dispute['quote'])
        config = json.loads(self.config.read_text())
        config['runners']['review'] = config['runners']['blind-behavior']
        self.config.write_text(json.dumps(config))
        result = workflow.invoke(self.root, 'T-1', 'one', 'review')
        seen = json.loads(Path(result['evidence']).read_text())['seen']
        self.assertEqual(seen['review_disputes'][0]['quote'], dispute['quote'])
        dispute['quote'] = 'not present'
        with self.assertRaises(FridayError):
            disputes.record(self.root, 'T-1', 'one', dispute)

    def test_blind_code_uses_isolated_workspace_and_zai_settings(self):
        config = json.loads(self.config.read_text())
        capture = ("import json,os,sys; request=json.load(sys.stdin); "
                   "print(json.dumps({'verdict':'pass','findings':[],'evidence':['checked'], "
                   "'request':request,'cwd':os.getcwd(),'token_file':os.environ.get('FRIDAY_ZAI_TOKEN_FILE'), "
                   "'token_key':os.environ.get('FRIDAY_ZAI_TOKEN_KEY')}))")
        config["runners"]["blind-code"] = {"model": "glm", "vendor": "zai", "command": ["python3", "-c", capture],
                                             "token_file": "/synthetic/token", "token_key": "SYNTHETIC_KEY"}
        self.config.write_text(json.dumps(config))
        self.write_plan([{"id": "one", "brief": "private", "criteria": ["works"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        result = workflow.invoke(self.root, "T-1", "one", "blind-code")
        output = json.loads(Path(result["evidence"]).read_text())
        self.assertEqual(output["request"]["vendor"], "zai")
        self.assertNotIn("worktree", output["request"])
        self.assertNotEqual(Path(output["cwd"]).resolve(), self.repo.resolve())
        self.assertEqual((output["token_file"], output["token_key"]), ("/synthetic/token", "SYNTHETIC_KEY"))

    def test_review_of_feedback_excludes_previously_published_changes(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()
        plan_base = git("rev-parse", "HEAD")
        (self.repo / "file").write_text("code\nprevious feature\n")
        git("add", "file"); git("commit", "-qm", "feature")
        published = git("rev-parse", "HEAD")
        (self.repo / "file").write_text("code\nprevious feature\nreview fix\n")
        git("add", "file"); git("commit", "-qm", "review fix")
        diff = workflow.review_diff(str(self.repo), {"plan_base": plan_base}, {"review_base": published})
        self.assertIn("+review fix", diff)
        self.assertNotIn("+previous feature", diff)

    def test_cycles_are_rejected_without_changing_card(self):
        self.write_plan([{"id": "one", "brief": "x", "criteria": ["a"], "depends_on": ["two"]},
                         {"id": "two", "brief": "y", "criteria": ["b"], "depends_on": ["one"]}])
        with self.assertRaises(FridayError) as error:
            workflow.load_plan(self.root, "T-1", self.plan)
        self.assertEqual(error.exception.code, "invalid_plan")
        self.assertEqual(state.read(self.root, "T-1")["stages"], {})

    def test_agent_team_is_validated_and_saved_with_the_plan(self):
        config = json.loads(self.config.read_text())
        config["agents"] = {"one": {"vendor": "openai", "model": "example", "roles": ["research"]}}
        self.config.write_text(json.dumps(config))
        assignment = {"id": "research", "agent": "one", "role": "research", "brief": "Inspect code", "skills": [], "depends_on": []}
        self.write_plan([{"id": "one", "brief": "x", "criteria": ["a"], "crew": [assignment]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        self.assertEqual(state.read(self.root, "T-1")["stages"]["one"]["crew"], [assignment])

    def test_behavior_brief_includes_test_data_policy(self):
        self.write_plan([{"id": "one", "brief": "implementation", "criteria": ["works"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        config = json.loads(self.config.read_text())
        config["behavior_test_policy"] = "Only staging; use ordinary application data."
        self.config.write_text(json.dumps(config))
        result = workflow.invoke(self.root, "T-1", "one", "blind-behavior")
        request = json.loads(Path(result["evidence"]).read_text())["seen"]
        self.assertEqual(request["behavior_test_policy"], config["behavior_test_policy"])

    def test_project_policy_reaches_developer_but_not_blind_browser(self):
        config = json.loads(self.config.read_text())
        config["engineering_policy"] = ["Use existing library capabilities."]
        self.config.write_text(json.dumps(config))
        self.write_plan([{"id": "one", "brief": "implementation", "criteria": ["works"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        workflow.approve_plan(self.root, "T-1")
        event = workflow.invoke(self.root, "T-1", "one", "develop")
        request = json.loads(Path(event["evidence"]).read_text())["seen"]
        self.assertEqual(request["engineering_policy"], config["engineering_policy"])
        event = workflow.invoke(self.root, "T-1", "one", "blind-behavior")
        self.assertNotIn("engineering_policy", json.loads(Path(event["evidence"]).read_text())["seen"])

    def test_changed_project_policy_invalidates_running_developer_result(self):
        config = json.loads(self.config.read_text())
        config["engineering_policy"] = ["Before."]
        script = self.root / "runner.py"
        script.write_text("import json,pathlib,sys\njson.load(sys.stdin)\np=pathlib.Path(" + repr(str(self.config)) + ")\n"
                          "v=json.loads(p.read_text()); v['engineering_policy']=['After.']; p.write_text(json.dumps(v))\nprint('done')\n")
        config["runners"]["develop"]["command"] = ["python3", str(script)]
        self.config.write_text(json.dumps(config))
        self.write_plan([{"id": "one", "brief": "implementation", "criteria": ["works"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        workflow.approve_plan(self.root, "T-1")
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, "T-1", "one", "develop")
        self.assertEqual(error.exception.code, "stale_evidence")

    def test_changed_project_policy_invalidates_existing_plan_before_development(self):
        config = json.loads(self.config.read_text())
        config["engineering_policy"] = ["Before."]
        self.config.write_text(json.dumps(config))
        self.write_plan([{"id": "one", "brief": "implementation", "criteria": ["works"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        workflow.approve_plan(self.root, "T-1")
        config["engineering_policy"] = ["After."]
        self.config.write_text(json.dumps(config))
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, "T-1", "one", "develop")
        self.assertEqual(error.exception.code, "stale_evidence")

    def test_same_model_cannot_review(self):
        self.write_plan([{"id": "one", "brief": "x", "criteria": ["a"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        config = json.loads(self.config.read_text())
        config["runners"]["blind-behavior"]["model"] = "a"
        self.config.write_text(json.dumps(config))
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, "T-1", "one", "blind-behavior")
        self.assertEqual(error.exception.code, "reviewer_not_independent")

    def test_review_cannot_pass_with_findings(self):
        self.write_plan([{"id": "one", "brief": "x", "criteria": ["a"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        config = json.loads(self.config.read_text())
        config["runners"]["blind-behavior"]["command"] = ["python3", "-c", "print('{\"verdict\":\"pass\",\"findings\":[\"problem\"]}')"]
        self.config.write_text(json.dumps(config))
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, "T-1", "one", "blind-behavior")
        self.assertEqual(error.exception.code, "invalid_verdict")

    def test_behavior_review_cannot_pass_without_observation(self):
        self.write_plan([{"id": "one", "brief": "x", "criteria": ["a"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        config = json.loads(self.config.read_text())
        config["runners"]["blind-behavior"]["command"] = ["python3", "-c", "print('{\"verdict\":\"pass\"}')"]
        self.config.write_text(json.dumps(config))
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, "T-1", "one", "blind-behavior")
        self.assertEqual(error.exception.code, "invalid_verdict")

    def test_manual_plan_needs_approval_and_replacement_invalidates_it(self):
        self.write_plan([{"id": "one", "brief": "x", "criteria": ["a"]}])
        workflow.load_plan(self.root, "T-1", self.plan)
        with self.assertRaises(FridayError) as error:
            workflow.invoke(self.root, "T-1", "one", "develop")
        self.assertEqual(error.exception.code, "plan_approval_required")
        workflow.approve_plan(self.root, "T-1")
        self.assertTrue(state.read(self.root, "T-1")["plan_approved"])
        workflow.load_plan(self.root, "T-1", self.plan, replace=True)
        card = state.read(self.root, "T-1")
        self.assertFalse(card["plan_approved"])
        self.assertEqual(card["plan_revision"], 2)
        self.assertTrue(any(event["type"] == "plan_replaced" for event in card["events"]))


if __name__ == "__main__":
    unittest.main()
