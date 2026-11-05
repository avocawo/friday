import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, doctor, profile, steps
from friday.errors import FridayError

ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
TOKEN = "//registry.example/:_authToken=SYNTHETIC-SECRET-TOKEN"
# An example project's scripts: Friday only runs them with placeholders filled in.
BUILD = "#!/bin/sh\nset -eu\nmkdir -p \"$1/bundle\"\necho \"built $2\" > \"$1/bundle/out.txt\"\necho stray > \"$1/stray.txt\"\n"
CHECK = "#!/bin/sh\necho \"image $1 state\"\nexit \"$2\"\n"


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=ENV).stdout


class StepTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.repo = base / "repo"
        git("init", "-q", "-b", "main", str(self.repo))
        (self.repo / "a.txt").write_text("a\n")
        git("add", ".", cwd=self.repo)
        git("commit", "-qm", "init", cwd=self.repo)
        self.worktree = base / "wt"
        git("worktree", "add", "-q", "-b", "T-1", str(self.worktree), cwd=self.repo)
        for name, text in (("build.sh", BUILD), ("check.sh", CHECK)):
            (base / name).write_text(text)
            (base / name).chmod(0o755)
        (base / "secret").mkdir()
        self.secret = base / "secret" / ".npmrc"
        self.secret.write_text(TOKEN)
        self.profile_path = base / "profile.json"
        self.data = {"name": "demo", "repository": "repo", "base_branch": "main", "steps": {
            "bundle": {"scope": "task", "when": "owner", "required": True, "run": ["{profile_dir}/build.sh", "{worktree}", "{key}"],
                       "commit": {"paths": ["bundle"], "message": "{key}: bundle"}},
            "image": {"scope": "profile", "when": "owner", "run": ["{profile_dir}/check.sh", "{secret.token}", "0"],
                      "check": ["{profile_dir}/check.sh", "img", "0"], "secrets": {"token": "secret/.npmrc"}}}}
        self.write()
        self.root = base / "home"
        card.create(self.root, "T-1", str(self.profile_path))
        card.update(self.root, "T-1", lambda value: value.update(worktree=str(self.worktree), branch="T-1"))

    def write(self, **changes):
        data = json.loads(json.dumps(self.data))
        for name, step in changes.items():
            data["steps"][name] = step
        self.profile_path.write_text(json.dumps(data))
        self.profile = profile.load(self.profile_path)

    def run_step(self, name, key="T-1", **options):
        with contextlib.redirect_stdout(io.StringIO()):
            return steps.run(self.root, self.profile, name, key, echo=lambda text: None, **options)

    def test_validation_names_what_is_wrong(self):
        base = self.data["steps"]["bundle"]
        for broken in (dict(base, run="build.sh"), dict(base, scope=None), dict(base, when="always"),
                       dict(base, run=["x", "{nope}"]), dict(base, scope="profile"),  # commit and {worktree} need a task
                       dict(base, check=["x"]), dict(base, commit={"paths": ["../x"], "message": "m"}),
                       dict(base, run=["x", "{secret.missing}"]), dict(base, env={"1A": "x"}), dict(base, extra=1)):
            with self.subTest(step=broken), self.assertRaises(FridayError):
                self.write(bundle=broken)

    def test_task_step_fills_placeholders_and_commits_only_its_paths(self):
        report = self.run_step("bundle")
        self.assertEqual((self.worktree / "bundle" / "out.txt").read_text(), "built T-1\n")
        self.assertEqual(git("log", "-1", "--format=%s", cwd=self.worktree).strip(), "T-1: bundle")
        self.assertEqual(git("show", "--name-only", "--format=", "HEAD", cwd=self.worktree).split(), ["bundle/out.txt"])
        self.assertEqual(git("status", "--porcelain", cwd=self.worktree), "?? stray.txt\n")
        self.assertEqual(report["commit"], git("rev-parse", "HEAD", cwd=self.worktree).strip())
        value = card.read(self.root, "T-1")
        self.assertEqual(value["steps"]["bundle"]["commit"], report["commit"])
        with self.assertRaises(FridayError):  # a dirty worktree is not committed over
            self.run_step("bundle")

    def test_a_failing_step_is_recorded_and_commits_nothing(self):
        self.write(bundle=dict(self.data["steps"]["bundle"], run=["false"]))
        with self.assertRaises(FridayError) as error:
            self.run_step("bundle")
        self.assertEqual(error.exception.code, "step_failed")
        self.assertIn("step_failed", [event["type"] for event in card.events(self.root, "T-1")])
        self.assertNotIn("steps", card.read(self.root, "T-1"))

    def test_dry_run_runs_nothing_and_container_steps_are_wrapped(self):
        report = self.run_step("bundle", dry_run=True)
        self.assertFalse((self.worktree / "bundle").exists())
        self.assertEqual(report["argv"][1:], [str(self.worktree), "T-1"])
        self.write(bundle=dict(self.data["steps"]["bundle"], container="app-1", env={"MODE": "dev"}))
        argv = self.run_step("bundle", dry_run=True)["argv"]
        self.assertEqual(argv[:5], ["docker", "exec", "-e", "MODE=dev", "app-1"])
        with self.assertRaises(FridayError):
            self.run_step("bundle", key=None)

    def test_secrets_are_passed_by_path_and_never_opened(self):
        real_open = open
        secret = str(self.secret.resolve())

        def guarded(path, *args, **kwargs):
            if str(path) == secret:
                raise AssertionError("Friday opened the secret")
            return real_open(path, *args, **kwargs)
        with patch("builtins.open", guarded):
            report = self.run_step("image", key=None, dry_run=True)
            self.run_step("image", key=None)
        self.assertEqual(report["argv"][1], secret)
        self.secret.unlink()
        with self.assertRaises(FridayError):
            self.run_step("image", key=None)

    def test_check_reports_ready_stale_and_missing_with_the_command(self):
        for code, state, mark in (("0", "ok", "ok"), ("2", "stale", "warning"), ("1", "missing", "missing")):
            self.write(image=dict(self.data["steps"]["image"], check=["{profile_dir}/check.sh", "img", code]))
            self.assertEqual(steps.check(self.profile, "image")[0], state)
            checks = []
            doctor.step_check(checks, "image", self.profile)
            self.assertEqual(checks[0]["status"], mark)
            if state != "ok":
                self.assertIn("friday step image --profile " + str(self.profile_path.resolve()), checks[0]["reason"])
            else:
                self.assertEqual(checks[0]["detail"], "image img state")

    def test_pending_lists_required_owner_task_steps_not_run(self):
        self.assertEqual(steps.pending(self.profile, card.read(self.root, "T-1")), ["bundle"])
        self.run_step("bundle")
        self.assertEqual(steps.pending(self.profile, card.read(self.root, "T-1")), [])


class DoctorGateTests(unittest.TestCase):
    def test_a_profile_with_a_preview_needs_a_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            git("init", "-q", str(Path(directory) / "repo"))
            preview = {"backend": "storybook", "command": ["serve"], "port": 6006}
            for extra, expected in (({}, "warning"), ({"preview": preview}, "missing")):
                path.write_text(json.dumps(dict({"name": "demo", "repository": "repo"}, **extra)))
                with patch("friday.doctor.models.cast", return_value=({}, [])):
                    report = doctor.inspect(path)
                gate = next(item for item in report["checks"] if item["name"] == "gate")
                self.assertEqual(gate["status"], expected, extra)


if __name__ == "__main__":
    unittest.main()
