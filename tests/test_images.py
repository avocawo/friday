import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import doctor, images, profile
from friday.errors import FridayError

REAL_RUN = subprocess.run
TOKEN = "//registry.example/:_authToken=SYNTHETIC-SECRET-TOKEN"


def git(*args, cwd=None):
    return REAL_RUN(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                    env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")).stdout


class FakeDocker:
    """Answers docker calls and passes git through; records each docker call with its keyword arguments."""

    def __init__(self, label=None):
        self.label = label
        self.calls = []

    def __call__(self, argv, *args, **kwargs):
        if argv[0] != "docker":
            return REAL_RUN(argv, *args, **kwargs)
        self.calls.append((list(argv), kwargs))
        if argv[1:3] == ["image", "inspect"]:
            if self.label is None:
                return subprocess.CompletedProcess(argv, 1, "", "No such image")
            return subprocess.CompletedProcess(argv, 0, self.label + "\n", "")
        context = Path(argv[-1])
        self.context = sorted(str(path.relative_to(context)) for path in context.rglob("*"))
        return subprocess.CompletedProcess(argv, 0)


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        origin = base / "origin"
        git("init", "-q", "-b", "master", str(origin))
        (origin / "Dockerfile").write_text("FROM node:20\n")
        (origin / "package-lock.json").write_text('{"lock": "master"}\n')
        git("add", ".", cwd=origin)
        git("commit", "-qm", "init", cwd=origin)
        self.showcase = base / "showcase"
        git("clone", "-q", str(origin), str(self.showcase))
        # The main checkout sits on someone else's branch with another lockfile and uncommitted work.
        git("checkout", "-q", "-b", "DVIZHDEV-1", cwd=self.showcase)
        (self.showcase / "package-lock.json").write_text('{"lock": "feature"}\n')
        git("commit", "-qam", "feature", cwd=self.showcase)
        (self.showcase / "wip.txt").write_text("wip\n")
        self.master_digest = hashlib.sha256(b'{"lock": "master"}\n').hexdigest()
        self.npmrc = base / "secret" / ".npmrc"
        self.npmrc.parent.mkdir()
        self.npmrc.write_text(TOKEN)
        self.profile_path = base / "profile.json"
        self.profile_path.write_text(json.dumps({
            "name": "kit", "repository": str(origin), "base_branch": "master", "worktree_root": str(base / "wt"),
            "stories": {"repository": "showcase", "base_branch": "master", "worktree_root": "showcase-wt",
                        "image": "showcase:friday",
                        "build": {"npmrc": "secret/.npmrc", "add_hosts": ["git.example:host-gateway"]}}}))

    def tearDown(self):
        self.directory.cleanup()

    def item(self):
        return images.select(profile.load(self.profile_path), "stories")[0]

    def test_profile_resolves_the_build_block(self):
        item = self.item()
        self.assertEqual(item["repository"], str(self.showcase.resolve()))
        self.assertEqual(item["npmrc"], str(self.npmrc.resolve()))
        self.assertEqual(item["ref"], "refs/remotes/origin/master")
        self.assertIn("friday image build --profile %s --only stories" % self.profile_path.resolve(), images.hint(item))

    def test_build_uses_the_base_branch_and_hands_the_npmrc_by_path(self):
        fake = FakeDocker()
        output = io.StringIO()
        with patch("friday.images.subprocess.run", fake), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = images.build(self.item())
        self.assertEqual(code, 0)
        argv, kwargs = fake.calls[-1]
        self.assertEqual(argv[:2], ["docker", "build"])
        self.assertIn("id=npmrc,src=" + str(self.npmrc.resolve()), argv)
        self.assertEqual(argv[argv.index("--add-host") + 1], "git.example:host-gateway")
        self.assertIn("%s=%s" % (images.LABEL, self.master_digest), argv)
        self.assertEqual(argv[argv.index("-t") + 1], "showcase:friday")
        self.assertNotIn("-f", argv)
        # The context is master's tree, not the checkout: no wip file, master's lockfile digest.
        self.assertEqual(fake.context, ["Dockerfile", "package-lock.json"])
        self.assertEqual(kwargs["env"]["DOCKER_BUILDKIT"], "1")
        self.assertNotIn("capture_output", kwargs)
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("SYNTHETIC-SECRET-TOKEN", json.dumps(fake.calls, default=str) + output.getvalue())
        self.assertEqual(git("branch", "--show-current", cwd=self.showcase).strip(), "DVIZHDEV-1")
        self.assertEqual(git("status", "--porcelain", cwd=self.showcase), "?? wip.txt\n")

    def test_build_never_opens_the_npmrc(self):
        fake = FakeDocker()
        real_open = open
        secret = str(self.npmrc.resolve())

        def guarded(path, *args, **kwargs):
            if str(path) == secret:
                raise AssertionError("Friday opened the .npmrc")
            return real_open(path, *args, **kwargs)
        with patch("friday.images.subprocess.run", fake), patch("builtins.open", guarded), \
                patch("pathlib.Path.read_bytes", side_effect=AssertionError("read")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(images.build(self.item()), 0)

    def test_missing_npmrc_and_override(self):
        self.npmrc.unlink()
        with patch("friday.images.subprocess.run", FakeDocker()):
            with self.assertRaises(FridayError) as error:
                images.build(self.item())
        self.assertIn("--npmrc", str(error.exception))
        other = Path(self.directory.name) / "other.npmrc"
        other.write_text(TOKEN)
        fake = FakeDocker()
        with patch("friday.images.subprocess.run", fake), contextlib.redirect_stdout(io.StringIO()):
            images.build(self.item(), npmrc=str(other))
        self.assertIn("id=npmrc,src=" + str(other), fake.calls[-1][0])

    def test_status_tells_missing_stale_and_ok_apart(self):
        item = self.item()
        cases = ((None, "missing"), ("", "stale"), ("<no value>", "stale"), ("0" * 64, "stale"), (self.master_digest, "ok"))
        for label, expected in cases:
            with patch("friday.images.subprocess.run", FakeDocker(label)):
                result = images.status(item)
            self.assertEqual(result["state"], expected, label)
            if expected != "ok":
                self.assertIn(images.hint(item), result["reason"])

    def test_doctor_names_the_command(self):
        for label, mark in ((None, "missing"), ("0" * 64, "warning")):
            with patch("friday.images.subprocess.run", FakeDocker(label)):
                checks = []
                doctor.image_check(checks, "stories_image", self.item())
            self.assertEqual(checks[0]["status"], mark)
            self.assertIn("friday image build --profile", checks[0]["reason"])

    def test_unfetched_base_branch_is_reported(self):
        git("update-ref", "-d", "refs/remotes/origin/master", cwd=self.showcase)
        with patch("friday.images.subprocess.run", FakeDocker()):
            with self.assertRaises(FridayError) as error:
                images.build(self.item())
        self.assertIn("fetch", str(error.exception))

    def test_gate_build_and_validation(self):
        data = json.loads(self.profile_path.read_text())
        data.update(gate_image="gate:local", gate_build={"repository": "showcase", "dockerfile": "gate.Dockerfile"})
        self.profile_path.write_text(json.dumps(data))
        item = images.select(profile.load(self.profile_path), "gate")[0]
        self.assertEqual(item["dockerfile"], str((Path(self.directory.name) / "gate.Dockerfile").resolve()))
        self.assertEqual(item["npmrc"], str(Path("~/.npmrc").expanduser()))
        fake = FakeDocker()
        with patch("friday.images.subprocess.run", fake), contextlib.redirect_stdout(io.StringIO()):
            images.build(item, npmrc=str(self.npmrc))
        self.assertEqual(fake.calls[-1][0][fake.calls[-1][0].index("-f") + 1], item["dockerfile"])
        for bad in ({"add_hosts": ["--privileged"]}, {"lockfile": "../x"}, {"unknown": 1}, {"npmrc": 5}):
            data["gate_build"] = bad
            self.profile_path.write_text(json.dumps(data))
            with self.assertRaises(FridayError):
                profile.load(self.profile_path)

    def test_only_selects_and_empty_profile_fails(self):
        with self.assertRaises(FridayError):
            images.select(profile.load(self.profile_path), "gate")


if __name__ == "__main__":
    unittest.main()
