import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import decisions, delivery, state
from friday.errors import FridayError
from friday.locks import locked


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "source").write_text("one")
        self.git("add", "source")
        self.git("commit", "-qm", "one")
        self.output = self.root / "output"
        self.config = self.root / "profile.json"
        command = ["python3", "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.stdin.read())", str(self.output)]
        self.config.write_text(json.dumps({"name": "test", "repository": str(self.repo), "actions": {"publish": command}}))
        state.create(self.root, "T-1", str(self.config))

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def test_explicit_approval_is_consumed_once(self):
        item = delivery.draft(self.root, "T-1", "publish", "reviewed text")
        with self.assertRaises(FridayError):
            delivery.execute(self.root, "T-1", item["id"])
        delivery.approve(self.root, "T-1", item["id"])
        result = delivery.execute(self.root, "T-1", item["id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.output.read_text(), "reviewed text")
        with self.assertRaises(FridayError):
            delivery.execute(self.root, "T-1", item["id"])

    def test_new_commit_invalidates_approval(self):
        item = delivery.draft(self.root, "T-1", "publish", "reviewed text")
        delivery.approve(self.root, "T-1", item["id"])
        (self.repo / "source").write_text("two")
        self.git("add", "source")
        self.git("commit", "-qm", "two")
        with self.assertRaises(FridayError) as error:
            delivery.execute(self.root, "T-1", item["id"])
        self.assertEqual(error.exception.code, "stale_approval")
        self.assertFalse(self.output.exists())

    def test_command_change_invalidates_approval(self):
        item = delivery.draft(self.root, "T-1", "publish", "reviewed text")
        delivery.approve(self.root, "T-1", item["id"])
        config = json.loads(self.config.read_text())
        config["actions"]["publish"].append("different")
        self.config.write_text(json.dumps(config))
        with self.assertRaises(FridayError) as error:
            delivery.execute(self.root, "T-1", item["id"])
        self.assertEqual(error.exception.code, "stale_approval")

    def test_delivery_waits_for_worktree_lock(self):
        item = delivery.draft(self.root, "T-1", "publish", "reviewed text")
        delivery.approve(self.root, "T-1", item["id"])
        finished = threading.Event()
        def run():
            delivery.execute(self.root, "T-1", item["id"])
            finished.set()
        with locked(self.root, "resource:branch:" + str(self.repo.resolve())):
            worker = threading.Thread(target=run)
            worker.start()
            self.assertFalse(finished.wait(0.2))
            self.assertFalse(self.output.exists())
        worker.join(5)
        self.assertTrue(finished.is_set())

    def test_adapter_file_change_invalidates_approval(self):
        adapter = self.root / "adapter.py"
        adapter.write_text("print('first')")
        config = json.loads(self.config.read_text())
        config["actions"]["publish"] = ["python3", str(adapter)]
        self.config.write_text(json.dumps(config))
        item = delivery.draft(self.root, "T-1", "publish", "reviewed text")
        delivery.approve(self.root, "T-1", item["id"])
        adapter.write_text("print('changed')")
        with self.assertRaises(FridayError) as error:
            delivery.execute(self.root, "T-1", item["id"])
        self.assertEqual(error.exception.code, "stale_approval")

    def test_changed_decision_invalidates_approval(self):
        item = delivery.draft(self.root, "T-1", "publish", "reviewed text")
        delivery.approve(self.root, "T-1", item["id"])
        decisions.set_decision(self.root, "T-1", "target", "changed")
        with self.assertRaises(FridayError) as error:
            delivery.execute(self.root, "T-1", item["id"])
        self.assertEqual(error.exception.code, "stale_approval")

    def test_gitlab_action_binds_target_and_private_credentials(self):
        token = self.root / "token"
        token.write_text("synthetic-token")
        token.chmod(0o600)
        config = json.loads(self.config.read_text())
        config["gitlab"] = {"base_url": "https://gitlab.example", "project": "team/project", "token_file": str(token)}
        config["actions"]["gitlab-draft"] = {"command": ["python3", "{gitlab_adapter}", "create-mr", "--base", "{gitlab_url}",
            "--project", "{gitlab_project}", "--source", "{branch}", "--target", "{target_branch}",
            "--token-file", "{gitlab_token_file}"], "bound_env": ["FRIDAY_GITLAB_TOKEN"]}
        self.config.write_text(json.dumps(config))
        state.update(self.root, "T-1", lambda card: card.update({"branch": "feature"}))
        with patch.dict(os.environ, {"FRIDAY_GITLAB_TOKEN": ""}):
            with self.assertRaises(FridayError) as error:
                delivery.draft(self.root, "T-1", "gitlab-draft", "reviewed")
            self.assertEqual(error.exception.code, "invalid_action")
            decisions.set_decision(self.root, "T-1", "target_branch", "base")
            item = delivery.draft(self.root, "T-1", "gitlab-draft", "reviewed")
            self.assertEqual(item["command"][item["command"].index("--target") + 1], "base")
            self.assertEqual(item["command"][item["command"].index("--project") + 1], "team/project")
            delivery.approve(self.root, "T-1", item["id"])
            token.write_text("rotated-token")
            with self.assertRaises(FridayError) as error:
                delivery.execute(self.root, "T-1", item["id"])
            self.assertEqual(error.exception.code, "stale_approval")

    def test_approved_actions_use_local_cli_and_mcp_without_network(self):
        token, org = self.root / "token", self.root / "org"
        token.write_text("synthetic-token"); token.chmod(0o600)
        org.write_text("synthetic-org"); org.chmod(0o600)
        mcp_log, glab_log = self.root / "mcp.json", self.root / "glab.json"
        fake_mcp = self.root / "mcp.py"
        fake_mcp.write_text("import json,sys,pathlib\n"
            "for line in sys.stdin:\n"
            " r=json.loads(line)\n"
            " if 'id' not in r: continue\n"
            " if r['method']=='tools/call':\n"
            "  pathlib.Path(" + repr(str(mcp_log)) + ").write_text(json.dumps(r['params']))\n"
            "  result={'structuredContent':{'key':'TEST-1'}}\n"
            " else: result={}\n"
            " print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)\n")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        glab = bin_dir / "glab"
        glab.write_text("#!/usr/bin/env python3\nimport json,pathlib,sys\n"
                        "pathlib.Path(" + repr(str(glab_log)) + ").write_text(json.dumps({'args':sys.argv[1:],'body':json.load(sys.stdin)}))\n"
                        "print(json.dumps({'iid':1}))\n")
        glab.chmod(0o755)
        config = json.loads(self.config.read_text())
        config.update(tracker={"mcp_command": ["python3", str(fake_mcp)], "token_file": str(token), "org_file": str(org)},
                      gitlab={"base_url": "https://gitlab.example", "project": "team/project", "token_file": str(token)})
        config["actions"]["tracker-create"] = {"command": ["python3", "{tracker_adapter}", "--mcp-command", "{tracker_mcp_command}",
            "--token-file", "{tracker_token_file}", "--org-file", "{tracker_org_file}"],
            "bound_files": ["{tracker_mcp_binary}", "{tracker_protocol}", "{credentials_module}"]}
        config["actions"]["gitlab-draft"] = {"command": ["python3", "{gitlab_adapter}", "create-mr", "--base", "{gitlab_url}",
            "--project", "{gitlab_project}", "--source", "{branch}", "--target", "{target_branch}", "--token-file", "{gitlab_token_file}"],
            "bound_files": ["{glab_binary}", "{credentials_module}"], "bound_env": ["PATH"]}
        self.config.write_text(json.dumps(config))
        state.update(self.root, "T-1", lambda card: card.update(branch="feature"))
        decisions.set_decision(self.root, "T-1", "target_branch", "main")
        with patch.dict(os.environ, {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "FRIDAY_GITLAB_TOKEN": "", "FRIDAY_TRACKER_TOKEN": ""}):
            for action, draft in (("tracker-create", '{"queue":"TEST","summary":"Task"}'),
                                  ("gitlab-draft", '{"title":"Title","description":"Body"}')):
                item = delivery.draft(self.root, "T-1", action, draft)
                delivery.approve(self.root, "T-1", item["id"])
                self.assertEqual(delivery.execute(self.root, "T-1", item["id"])["status"], "succeeded")
        self.assertEqual(json.loads(mcp_log.read_text())["arguments"], {"queue": "TEST", "summary": "Task"})
        glab_call = json.loads(glab_log.read_text())
        self.assertEqual(glab_call["body"]["description"], "Body")
        self.assertIn("projects/team%2Fproject/merge_requests", glab_call["args"])
        self.assertNotIn("synthetic-token", glab_log.read_text())

    def test_relative_executable_is_bound_in_worktree(self):
        (self.repo / ".git" / "info" / "exclude").write_text("action.sh\n")
        script = self.repo / "action.sh"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
        config = json.loads(self.config.read_text())
        config["actions"]["publish"] = ["./action.sh"]
        self.config.write_text(json.dumps(config))
        item = delivery.draft(self.root, "T-1", "publish", "reviewed text")
        delivery.approve(self.root, "T-1", item["id"])
        script.write_text("#!/bin/sh\nexit 1\n")
        with self.assertRaises(FridayError) as error:
            delivery.execute(self.root, "T-1", item["id"])
        self.assertEqual(error.exception.code, "stale_approval")

    def test_virtual_environment_executable_keeps_its_prefix(self):
        import sys
        environment = self.root / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True)
        interpreter = environment / "bin" / "python"
        config = json.loads(self.config.read_text())
        config["actions"]["publish"] = [str(interpreter), "-c",
            "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.prefix)", str(self.output)]
        self.config.write_text(json.dumps(config))
        item = delivery.draft(self.root, "T-1", "publish", "reviewed text")
        delivery.approve(self.root, "T-1", item["id"])
        delivery.execute(self.root, "T-1", item["id"])
        self.assertEqual(Path(self.output.read_text()).resolve(), environment.resolve())


if __name__ == "__main__":
    unittest.main()
