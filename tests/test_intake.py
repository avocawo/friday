import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import intake, profile, state
from friday.errors import FridayError


class IntakeTests(unittest.TestCase):
    def test_worktree_uses_fetched_base_and_can_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin = root / "origin.git"
            repo = root / "repo"
            subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            (repo / "file").write_text("code")
            git("add", "file")
            git("commit", "-qm", "initial")
            git("branch", "-M", "main")
            git("remote", "add", "origin", str(origin))
            git("push", "-q", "origin", "main")
            path = root / "profile.json"
            path.write_text(json.dumps({"name": "test", "repository": str(repo),
                                        "worktree_root": str(root / "worktrees"), "worktree_layout": "branch", "base_branch": "main"}))
            state.create(root, "ABC-1", str(path))
            original_update = state.update
            def interrupted(*args, **kwargs):
                if (root / "worktrees" / "friday" / "ABC-1").exists():
                    raise OSError("simulated crash after worktree creation")
                return original_update(*args, **kwargs)
            with patch("friday.intake.state.update", side_effect=interrupted):
                with self.assertRaises(OSError):
                    intake.prepare(root, "ABC-1", profile.load(path))
            self.assertIn("worktree_intent", state.read(root, "ABC-1"))
            worktree = intake.prepare(root, "ABC-1", profile.load(path))
            self.assertEqual(Path(worktree), root.resolve() / "worktrees" / "friday" / "ABC-1")
            self.assertEqual((Path(worktree) / "file").read_text(), "code")
            self.assertEqual(intake.prepare(root, "ABC-1", profile.load(path)), worktree)
            self.assertEqual(state.read(root, "ABC-1")["branch"], "friday/ABC-1")
            updated = json.loads(path.read_text())
            updated["branch_template"] = "{key}"
            path.write_text(json.dumps(updated))
            state.create(root, "ABC-2", str(path))
            other = intake.prepare(root, "ABC-2", profile.load(path))
            self.assertEqual(Path(other), root.resolve() / "worktrees" / "ABC-2")
            self.assertEqual(state.read(root, "ABC-2")["branch"], "ABC-2")

    def test_tracker_requires_credentials_without_sending_request(self):
        config = {"tracker": {"base_url": "https://example.invalid", "token_env": "NEVER_CONFIGURED_TOKEN",
                              "org_env": "NEVER_CONFIGURED_ORG"}}
        with self.assertRaises(FridayError) as error:
            intake.ticket(config, "ABC-1")
        self.assertEqual(error.exception.code, "credentials_missing")

    def test_tracker_reads_v3_issue_and_related_context(self):
        config = {"tracker": {"base_url": "https://tracker.example", "token_env": "TOKEN_FOR_TEST",
                              "org_env": "ORG_FOR_TEST"}}
        response = {"key": "ABC-1", "summary": "Issue", "comments": [{"text": "comment"}]}
        with patch.dict("os.environ", {"TOKEN_FOR_TEST": "secret", "ORG_FOR_TEST": "123"}), \
             patch("friday.intake.http.read_json", return_value=response) as get:
            issue = intake.ticket(config, "ABC-1")
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
            with patch("friday.intake.http.read_json", return_value={"key": "ABC-1"}) as get:
                intake.ticket(config, "ABC-1")
            self.assertEqual(get.call_args.args[0].get_header("X-org-id"), "123")
            self.assertEqual(get.call_args.args[0].get_header("Authorization"), "OAuth synthetic-token")


if __name__ == "__main__":
    unittest.main()
