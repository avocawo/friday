import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, dist, profile
from friday.errors import FridayError

REAL_RUN = subprocess.run
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def git(*args, cwd=None):
    return REAL_RUN(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=ENV).stdout


class FakeContainer:
    """docker inspect/exec against a host directory mounted at /app/deps/vue-uikit; git passes through."""

    def __init__(self, checkout, running=True, fail_build=False, output=None):
        self.checkout, self.running, self.fail_build = Path(checkout), running, fail_build
        self.output = output or {"vue-uikit.es.js": "export const built = '{head}';\n", "FileDropzone.d.ts": "export {};\n"}
        self.calls = []

    def host(self, path):
        return self.checkout / path[len("/app/deps/vue-uikit/"):]

    def __call__(self, argv, *args, **kwargs):
        if argv[0] != "docker":
            return REAL_RUN(argv, *args, **kwargs)
        self.calls.append(list(argv))
        if argv[1] == "inspect":
            return subprocess.CompletedProcess(argv, 0, "true\n" if self.running else "false\n", "")
        command = argv[argv.index("nfront-frontend-1") + 1:]
        if command[:2] == ["rm", "-rf"]:
            for path in command[2:]:
                shutil.rmtree(self.host(path), ignore_errors=True)
        elif command[-2:] == ["run", "build"]:
            if self.fail_build:
                (self.checkout / "dist" / "vue-uikit.es.js").write_text("half-written\n")
                return subprocess.CompletedProcess(argv, 1)
            head = git("rev-parse", "HEAD", cwd=self.checkout).strip()
            for name, text in self.output.items():
                (self.checkout / "dist" / name).write_text(text.replace("{head}", head))
        elif command[:2] == ["npm", "ci"]:
            (self.checkout / "node_modules" / "vue").mkdir(parents=True, exist_ok=True)
            (self.checkout / "node_modules" / "@vue").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(argv, 0)


class DistTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        origin = base / "origin.git"
        git("init", "-q", "--bare", "-b", "master", str(origin))
        self.checkout = base / "nfront" / "deps" / "vue-uikit"
        git("clone", "-q", str(origin), str(self.checkout))
        (self.checkout / ".gitignore").write_text("node_modules\n")
        (self.checkout / "src.ts").write_text("export const a = 1;\n")
        (self.checkout / "dist").mkdir()
        (self.checkout / "dist" / "vue-uikit.es.js").write_text("old\n")
        git("add", ".", cwd=self.checkout)
        git("commit", "-qm", "1.0.97: Собран dist", cwd=self.checkout)
        git("tag", "1.0.98", cwd=self.checkout)
        git("push", "-q", "origin", "HEAD:master", "--tags", cwd=self.checkout)
        git("fetch", "-q", "origin", cwd=self.checkout)
        git("checkout", "-q", "--detach", "1.0.98", cwd=self.checkout)
        self.saved = git("rev-parse", "HEAD", cwd=self.checkout).strip()
        self.worktree = base / "wt" / "T-1"
        git("worktree", "add", "-q", "-b", "T-1", str(self.worktree), "1.0.98", cwd=self.checkout)
        (self.worktree / "src.ts").write_text("export const a = 2;\n")
        git("commit", "-qam", "T-1 M1.1: change", cwd=self.worktree)
        self.profile_path = base / "profile.json"
        self.profile_path.write_text(json.dumps({
            "name": "kit", "repository": str(self.checkout), "base_branch": "master", "worktree_root": "wt",
            "dist": {"checkout": "nfront/deps/vue-uikit", "container": "nfront-frontend-1"}}))
        self.profile = profile.load(self.profile_path)
        self.root = base / "home"
        card.create(self.root, "T-1", str(self.profile_path))
        card.update(self.root, "T-1", lambda value: value.update(worktree=str(self.worktree), branch="T-1"))
        self.output = []

    def build(self, fake, **options):
        with patch("friday.dist.subprocess.run", fake):
            return dist.build(self.root, "T-1", self.profile, echo=self.output.append, **options)

    def assert_restored(self):
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.checkout).strip(), self.saved)
        self.assertEqual(git("status", "--porcelain", cwd=self.checkout), "")

    def test_profile_resolves_and_validates(self):
        self.assertEqual(self.profile["dist"]["checkout"], str(self.checkout.resolve()))
        data = json.loads(self.profile_path.read_text())
        for bad in ({"checkout": "x"}, dict(data["dist"], cleanup=["../x"]), dict(data["dist"], env={"A=B": "c"}),
                    dict(data["dist"], build="npm run build")):
            self.profile_path.write_text(json.dumps(dict(data, dist=bad)))
            with self.assertRaises(FridayError):
                profile.load(self.profile_path)

    def test_builds_in_the_container_commits_dist_and_restores_the_checkout(self):
        fake = FakeContainer(self.checkout)
        task_head = git("rev-parse", "HEAD", cwd=self.worktree).strip()
        report = self.build(fake)
        execs = [call for call in fake.calls if call[1] == "exec"]
        self.assertEqual(execs[0], ["docker", "exec", "-e", "NODE_ENV=development", "nfront-frontend-1",
                                    "npm", "ci", "--prefix", "/app/deps/vue-uikit"])
        self.assertEqual(execs[1][-5:], ["npm", "--prefix", "/app/deps/vue-uikit", "run", "build"])
        self.assertEqual(execs[2], ["docker", "exec", "nfront-frontend-1", "rm", "-rf", "/app/deps/vue-uikit/node_modules/vue",
                                    "/app/deps/vue-uikit/node_modules/@vue"])
        self.assertEqual(git("log", "-1", "--format=%s", cwd=self.worktree).strip(), "T-1: Собран dist")
        self.assertEqual((self.worktree / "dist" / "vue-uikit.es.js").read_text(), "export const built = '%s';\n" % task_head)
        self.assertTrue((self.worktree / "dist" / "FileDropzone.d.ts").is_file())
        self.assert_restored()
        self.assertFalse((self.checkout / "node_modules" / "vue").exists())
        self.assertIn("HMR", self.output[0])
        self.assertEqual(report["previous"], [git("log", "-1", "--format=%h %s", "origin/master", cwd=self.checkout).strip()])
        self.assertIn("переименования минификатора", dist.text(report))
        self.assertEqual(card.read(self.root, "T-1")["dist"]["state"], "built")

    def test_a_failed_build_still_restores_the_checkout(self):
        with self.assertRaises(FridayError) as error:
            self.build(FakeContainer(self.checkout, fail_build=True))
        self.assertEqual(error.exception.code, "dist_failed")
        self.assert_restored()
        self.assertEqual(git("log", "-1", "--format=%s", cwd=self.worktree).strip(), "T-1 M1.1: change")

    def test_refuses_a_dirty_checkout_or_a_stopped_container(self):
        (self.checkout / "src.ts").write_text("someone's work\n")
        fake = FakeContainer(self.checkout)
        with self.assertRaises(FridayError):
            self.build(fake)
        self.assertEqual(fake.calls, [])
        git("checkout", "--", "src.ts", cwd=self.checkout)
        fake = FakeContainer(self.checkout, running=False)
        with self.assertRaises(FridayError) as error:
            self.build(fake)
        self.assertEqual(error.exception.code, "environment_unavailable")
        self.assertEqual([call[1] for call in fake.calls], ["inspect"])
        self.assert_restored()

    def test_dry_run_changes_nothing(self):
        fake = FakeContainer(self.checkout)
        report = self.build(fake, dry_run=True)
        self.assertEqual(fake.calls, [])
        self.assertEqual(report["restore"], self.saved)
        self.assertIn("npm --prefix /app/deps/vue-uikit run build", "\n".join(report["steps"]))
        self.assert_restored()

    def test_check_base_ignores_volatile_paths(self):
        (self.checkout / "dist" / "vue-uikit.es.js").write_text("x\nconst __file = '/host/a.vue';\n")
        git("commit", "-qam", "Собран dist", cwd=self.checkout)
        git("push", "-q", "origin", "HEAD:master", cwd=self.checkout)
        git("fetch", "-q", "origin", cwd=self.checkout)
        git("checkout", "-q", "--detach", "1.0.98", cwd=self.checkout)
        same = FakeContainer(self.checkout, output={"vue-uikit.es.js": "x\nconst __file = '/app/a.vue';\n"})
        self.assertTrue(self.build(same, check_base=True)["reproduced"])
        other = FakeContainer(self.checkout, output={"vue-uikit.es.js": "y\nconst __file = '/app/a.vue';\n"})
        report = self.build(other, check_base=True)
        self.assertEqual((report["reproduced"], report["differing_lines"]), (False, ["-x", "+y"]))
        extra = FakeContainer(self.checkout, output={"vue-uikit.es.js": "x\nconst __file = '/app/a.vue';\n", "New.d.ts": "n\n"})
        self.assertEqual(self.build(extra, check_base=True)["differing_lines"], ["?? dist/New.d.ts"])
        self.assert_restored()
        self.assertNotIn("dist", card.read(self.root, "T-1"))


if __name__ == "__main__":
    unittest.main()
