import contextlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import autopilot, dispatch, profile, state, workflow
from friday.errors import FridayError


class DispatchTests(unittest.TestCase):
    def setUp(self):
        preview_mock = patch('friday.isolated_preview.open_preview', side_effect=lambda *_: contextlib.nullcontext('http://test.localhost:1234/'))
        preview_mock.start()
        self.addCleanup(preview_mock.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()
        self.git = git
        git("init", "-q")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        git("branch", "-M", "friday/T-1")
        (self.repo / "source").write_text("old\n")
        git("add", "source")
        git("commit", "-qm", "initial")
        self.runner = self.root / "runner.py"
        self.runner.write_text("import json,pathlib,sys\nr=json.load(sys.stdin)\n"
                               "if r['role']=='develop':\n"
                               " assert r['upstream']['research'].strip()=='research evidence'\n"
                               " pathlib.Path('source').write_text('new\\n')\n"
                               "if r['role']=='review':\n"
                               " assert '+new' in r['diff']\n"
                               " print(json.dumps({'verdict':'pass','findings':[],'evidence':['checked']}))\n"
                               "else: print('research evidence' if r['role']=='research' else 'changed')\n")
        roles = {"scout": ("zai", "research"), "coder": ("openai", "develop"), "reviewer": ("anthropic", "review")}
        agents = {name: {"vendor": vendor, "model": name, "roles": [role], "command": ["python3", str(self.runner)]}
                  for name, (vendor, role) in roles.items()}
        self.config = self.root / "profile.json"
        self.config.write_text(json.dumps({"name": "test", "repository": str(self.repo), "agents": agents}))
        state.create(self.root, "T-1", str(self.config), mode="auto")
        state.update(self.root, "T-1", lambda card: card.update({"worktree": str(self.repo), "branch": "friday/T-1"}))
        team = [{"id": "research", "agent": "scout", "role": "research", "brief": "inspect", "skills": [], "depends_on": []},
                {"id": "code", "agent": "coder", "role": "develop", "brief": "change", "skills": [], "depends_on": ["research"]},
                {"id": "review", "agent": "reviewer", "role": "review", "brief": "review", "skills": [], "depends_on": ["code"]}]
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({"stages": [{"id": "one", "brief": "update", "criteria": ["works"], "files": ["source"], "crew": team}]}))
        workflow.load_plan(self.root, "T-1", plan)

    def test_dispatches_in_order_commits_and_resumes_without_repeating(self):
        with patch("friday.isolation.require_runner"):
            result = dispatch.run(self.root, "T-1", "one")
            self.assertEqual(list(result["assignments"]), ["research", "code", "review"])
            self.assertEqual((self.repo / "source").read_text(), "new\n")
            self.assertEqual(self.git("status", "--porcelain"), "")
            self.assertEqual(dispatch.run(self.root, "T-1", "one"), result)
        events = state.read(self.root, "T-1")["events"]
        self.assertEqual([item["vendor"] for item in events if item.get("type") == "model_run"], ["zai", "openai", "anthropic"])

    def test_builtin_adapters_dispatch_three_vendors_without_network(self):
        codex = self.root / "codex"
        codex.write_text("#!/usr/bin/env python3\nimport json,pathlib,sys\n"
                         "if sys.argv[1:4] == ['mcp','list','--json']:\n print('[]'); sys.exit(0)\n"
                         "assert '\"role\": \"develop\"' in sys.stdin.read()\n"
                         "pathlib.Path('source').write_text('new\\n')\n"
                         "pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('implemented')\n")
        claude = self.root / "claude"
        claude.write_text("#!/usr/bin/env python3\nimport json,os,sys\nprompt=sys.stdin.read()\n"
                          "if os.environ.get('ANTHROPIC_BASE_URL'):\n"
                          " assert os.environ['ANTHROPIC_BASE_URL']=='https://api.z.ai/api/anthropic'\n"
                          " assert '\"role\": \"research\"' in prompt\n"
                          " print(json.dumps({'result':'research evidence'}))\n"
                          "else:\n"
                          " assert '\"role\": \"review\"' in prompt\n"
                          " print(json.dumps({'structured_output':{'verdict':'pass','findings':[],'evidence':['reviewed']}}))\n")
        codex.chmod(0o755)
        claude.chmod(0o755)
        config = json.loads(self.config.read_text())
        config["agents"] = {
            "scout": {"vendor": "zai", "model": "glm", "roles": ["research"], "command": ["python3", "{claude_adapter}"]},
            "coder": {"vendor": "openai", "model": "codex", "roles": ["develop"], "command": ["python3", "{codex_adapter}"]},
            "reviewer": {"vendor": "anthropic", "model": "claude", "roles": ["review"], "command": ["python3", "{claude_adapter}"]},
        }
        self.config.write_text(json.dumps(config))
        workflow.load_plan(self.root, "T-1", self.root / "plan.json", replace=True)
        env = {"PATH": str(self.root) + os.pathsep + os.environ.get("PATH", ""), "FRIDAY_ZAI_TOKEN": "synthetic"}
        with patch.dict(os.environ, env), patch("friday.isolation.require_runner"):
            result = dispatch.run(self.root, "T-1", "one")
        self.assertEqual(list(result["assignments"]), ["research", "code", "review"])
        self.assertEqual((self.repo / "source").read_text(), "new\n")

    def test_interrupted_agent_is_not_replayed(self):
        with patch("friday.isolation.require_runner"), patch("friday.dispatch.invoke", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                dispatch.run(self.root, "T-1", "one")
        with patch("friday.isolation.require_runner"):
            with self.assertRaises(FridayError) as error:
                dispatch.run(self.root, "T-1", "one")
        self.assertEqual(error.exception.code, "dispatch_interrupted")

    def test_browser_environment_failure_retries_only_the_browser_assignment(self):
        config = json.loads(self.config.read_text())
        config["agents"]["browser"] = {"vendor": "openai", "model": "browser", "roles": ["blind-behavior"],
                                       "command": ["python3", str(self.runner)]}
        self.config.write_text(json.dumps(config))
        plan = json.loads((self.root / "plan.json").read_text())
        plan["stages"][0]["crew"].append({"id": "browser", "agent": "browser", "role": "blind-behavior", "brief": "verify",
                                            "skills": [], "depends_on": ["code"]})
        (self.root / "plan.json").write_text(json.dumps(plan))
        workflow.load_plan(self.root, "T-1", self.root / "plan.json", replace=True)
        evidence = self.root / "result.json"
        calls = []
        def invoke(root, key, stage, item, selected, target, upstream, feedback):
            calls.append(item["role"])
            if item["role"] == "blind-behavior" and calls.count("blind-behavior") == 1:
                evidence.write_text(json.dumps({"verdict": "fail", "findings": ["ENVIRONMENT-UNAVAILABLE: browser"]}))
                return {"verdict": "fail", "evidence": str(evidence)}
            evidence.write_text("ok")
            return {"verdict": "pass" if item["role"] in ("review", "blind-behavior") else None, "evidence": str(evidence)}
        with patch("friday.isolation.require_runner"), patch("friday.dispatch.invoke", side_effect=invoke):
            with self.assertRaises(FridayError) as error:
                dispatch.run(self.root, "T-1", "one")
            self.assertEqual(error.exception.code, "environment_unavailable")
            dispatch.run(self.root, "T-1", "one")
        self.assertEqual(calls.count("develop"), 1)
        self.assertEqual(calls.count("blind-behavior"), 2)

    def test_changed_runner_invalidates_completed_dispatch(self):
        with patch("friday.isolation.require_runner"):
            dispatch.run(self.root, "T-1", "one")
            self.runner.write_text(self.runner.read_text() + "\n# changed\n")
            with self.assertRaises(FridayError) as error:
                dispatch.run(self.root, "T-1", "one")
        self.assertEqual(error.exception.code, "stale_evidence")

    def test_changed_agent_catalog_invalidates_approved_team_before_launch(self):
        config = json.loads(self.config.read_text())
        config["agents"]["reviewer"]["model"] = "replacement"
        self.config.write_text(json.dumps(config))
        with patch("friday.isolation.require_runner"), patch("friday.dispatch.invoke") as invoke:
            with self.assertRaises(FridayError) as error:
                dispatch.run(self.root, "T-1", "one")
        self.assertEqual(error.exception.code, "stale_evidence")
        invoke.assert_not_called()

    def test_project_policy_is_forwarded_only_to_contextual_roles(self):
        self.runner.write_text("import json,sys\nr=json.load(sys.stdin)\n"
                               "print(json.dumps({'verdict':'pass','findings':[],'evidence':['observed'],'request':r}))\n")
        selected = profile.load(self.config)
        selected["engineering_policy"] = ["Find the project precedent."]
        selected["agents"]["all"] = {"vendor": "openai", "model": "m", "roles": [], "command": ["python3", str(self.runner)]}
        with patch("friday.isolation.require_browser"):
            for role in ("research", "develop", "review", "blind-code", "blind-behavior"):
                item = {"id": role, "agent": "all", "role": role, "brief": "check", "skills": [], "depends_on": []}
                event = dispatch.invoke(self.root, "T-1", "one", item, selected, str(self.repo), {})
                request = json.loads(Path(event["evidence"]).read_text())["request"]
                if role in ("research", "develop", "review"):
                    self.assertEqual(request["engineering_policy"], selected["engineering_policy"])
                else:
                    self.assertNotIn("engineering_policy", request)

    def test_changing_project_policy_invalidates_team_checkpoint(self):
        with patch("friday.isolation.require_runner"):
            dispatch.run(self.root, "T-1", "one")
            config = json.loads(self.config.read_text())
            config["engineering_policy"] = ["A changed rule."]
            self.config.write_text(json.dumps(config))
            with self.assertRaises(FridayError) as error:
                dispatch.run(self.root, "T-1", "one")
        self.assertEqual(error.exception.code, "stale_evidence")

    def test_changed_runner_during_agent_execution_blocks_result(self):
        def changed(*args):
            self.runner.write_text(self.runner.read_text() + "\n# changed while running\n")
            return {"evidence": str(self.root / "evidence"), "verdict": None}
        with patch("friday.isolation.require_runner"), patch("friday.dispatch.invoke", side_effect=changed):
            with self.assertRaises(FridayError) as error:
                dispatch.run(self.root, "T-1", "one")
        self.assertEqual(error.exception.code, "stale_evidence")

    def test_failed_review_is_returned_to_developer_and_rechecked(self):
        self.runner.write_text("import json,pathlib,sys\nr=json.load(sys.stdin)\n"
                               "if r['role']=='develop': pathlib.Path('source').write_text('fixed' if r.get('feedback') else 'first')\n"
                               "if r['role']=='review':\n"
                               " bad=pathlib.Path('source').read_text()=='first'\n"
                               " print(json.dumps({'verdict':'fail' if bad else 'pass','findings':['fix it'] if bad else [],'evidence':['checked']}))\n"
                               "else: print('work')\n")
        with patch("friday.isolation.require_runner"):
            result = dispatch.run(self.root, "T-1", "one")
        self.assertEqual((self.repo / "source").read_text(), "fixed")
        self.assertEqual(result["assignments"]["review"]["verdict"], "pass")
        reviews = [event["verdict"] for event in state.read(self.root, "T-1")["events"] if event.get("role") == "review"]
        self.assertEqual(reviews, ["fail", "pass"])

    def test_repeated_failed_review_stops_and_preserves_the_last_result(self):
        self.runner.write_text("import json,pathlib,sys\nr=json.load(sys.stdin)\n"
                               "if r['role']=='develop': pathlib.Path('source').write_text('new')\n"
                               "if r['role']=='review': print(json.dumps({'verdict':'fail','findings':['still broken'],'evidence':['checked']}))\n"
                               "else: print('work')\n")
        with patch("friday.isolation.require_runner"):
            for _ in range(2):
                with self.assertRaises(FridayError) as error:
                    dispatch.run(self.root, "T-1", "one")
                self.assertEqual(error.exception.code, "review_failed")
        reviews = [event for event in state.read(self.root, "T-1")["events"] if event.get("role") == "review"]
        self.assertEqual(len(reviews), 3)
        self.assertTrue(Path(reviews[-1]["evidence"]).is_file())

    def test_go_routes_team_then_runs_gate_and_closes_stage(self):
        self.runner.write_text("import json,pathlib,sys\nr=json.load(sys.stdin)\n"
                               "if r['role']=='develop': pathlib.Path('source').write_text('new')\n"
                               "if r['role'] in ('review','blind-code','blind-behavior'):\n"
                               " print(json.dumps({'verdict':'pass','findings':[],'evidence':['observed']}))\n"
                               "else: print('work')\n")
        config = json.loads(self.config.read_text())
        config["agents"]["blind"] = {"vendor": "zai", "model": "blind", "roles": ["blind-code", "blind-behavior"],
                                       "command": ["python3", str(self.runner)]}
        self.config.write_text(json.dumps(config))
        card = state.read(self.root, "T-1")
        team = card["stages"]["one"]["crew"]
        team += [{"id": "blindcode", "agent": "blind", "role": "blind-code", "brief": "review", "skills": [], "depends_on": ["code"]},
                 {"id": "browser", "agent": "blind", "role": "blind-behavior", "brief": "test", "skills": [], "depends_on": ["code"]}]
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({"stages": [{"id": "one", "brief": "update", "criteria": ["works"], "files": ["source"], "crew": team}]}))
        workflow.load_plan(self.root, "T-1", plan, replace=True)
        def close(root, key, name, method):
            state.update(root, key, lambda item: item["stages"][name].update(status="closed"))
        with patch("friday.isolation.require_runner"), patch("friday.isolation.require_browser"), \
             patch("friday.autopilot.check_preflight"), \
             patch("friday.gate.execute", return_value={"status": "passed"}) as gate, \
             patch("friday.stages.close", side_effect=close), patch("friday.journal.sync"):
            result = autopilot.run(self.root, "T-1")
        self.assertEqual(result["remaining"], [])
        self.assertEqual(result["completed"][0]["stage"], "one")
        gate.assert_called_once()
