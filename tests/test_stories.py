import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, final, milestone, plan, profile, runner, stories, workspace
from friday.errors import FridayError

# Each call takes the next entry of calls.json; "ops" write files and commit, each in a directory relative to cwd.
FAKE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
directory = os.environ["FAKE_DIR"]
prompt = sys.stdin.read()
counter = os.path.join(directory, "counter")
index = int(open(counter).read()) if os.path.exists(counter) else 0
open(counter, "w").write(str(index + 1))
with open(os.path.join(directory, "call-%d.json" % index), "w") as handle:
    json.dump({"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd()}, handle)
call = json.load(open(os.path.join(directory, "calls.json")))[index]
env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
for op in call.get("ops", []):
    where = os.path.join(os.getcwd(), op.get("dir", "."))
    for path, content in op.get("write", {}).items():
        target = os.path.join(where, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        open(target, "w").write(content)
    for message in op.get("commit", []):
        subprocess.run(["git", "-C", where, "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", where, "commit", "-qm", message], check=True, env=env)
print(json.dumps({"type": "system", "subtype": "init", "session_id": call["session"]}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": call["session"], "result": "",
                  "structured_output": call["answer"], "total_cost_usd": 0.01, "num_turns": 1,
                  "usage": {"input_tokens": 10, "output_tokens": 10}}), flush=True)
'''

MODELS = {
    "big": {"model": "big-1", "vendor": "alpha", "driver": "claude", "tier": "frontier"},
    "rival": {"model": "rival-1", "vendor": "beta", "driver": "claude", "tier": "frontier"},
    "worker": {"model": "worker-1", "vendor": "gamma", "driver": "claude", "tier": "strong"},
}
PLAN = {"summary": "Компонент и история.", "rules": [], "assumptions": [], "questions": [], "responses": [],
        "milestones": [{"id": "M1", "title": "Кнопка", "goal": "Кнопка и её история", "complexity": "clear", "ui": True,
                        "parallel": False, "depends_on": [], "files": ["src/Button.vue", "stories:src/stories/Button.stories.ts"],
                        "stages": [{"id": "M1.1", "brief": "Кнопка", "steps": [], "files": ["src/Button.vue"],
                                    "new_files": ["stories:src/stories/Button.stories.ts"], "estimate_lines": 20,
                                    "criteria": [{"text": "История controls-button--default показывает кнопку", "check": "behavior"}]}]}]}
DONE = {"summary": "Сделано.", "stages_done": ["M1.1"], "extra_files": [], "disputes": [], "checks": "passed", "checks_note": ""}
PASS = {"verdict": "pass", "criteria_results": [{"criterion": "c", "status": "pass", "evidence": "f:1"}], "findings": []}


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")).stdout


def repository(base, name, files):
    origin, clone = base / (name + ".git"), base / name
    git("init", "-q", "--bare", "-b", "master", str(origin))
    git("clone", "-q", str(origin), str(clone))
    for path, content in files.items():
        (clone / path).parent.mkdir(parents=True, exist_ok=True)
        (clone / path).write_text(content)
    git("add", ".", cwd=clone)
    git("commit", "-qm", "init", cwd=clone)
    git("push", "-q", "origin", "HEAD:master", cwd=clone)
    return clone


class StoriesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root, self.fake = base / "home", base / "fake"
        (self.fake / "bin").mkdir(parents=True)
        (self.fake / "bin" / "claude").write_text(FAKE)
        (self.fake / "bin" / "claude").chmod(0o755)
        kit = repository(base, "kit", {"src/Button.vue": "<template><button /></template>\n", "package.json": "{}\n"})
        self.showcase_repo = repository(base, "storybook", {".gitignore": "deps/*\n!deps/.gitkeep\n", "deps/.gitkeep": "",
                                                            "src/stories/Input.stories.ts": "export default {};\n"})
        self.profile_path = base / "profile.json"
        self.profile = {"name": "kit", "repository": str(kit), "base_branch": "master", "worktree_root": str(base / "wt"),
                        "branch_template": "{key}", "models": MODELS,
                        "roles": {"plan": ["big"], "plan-review": ["rival"], "develop": ["worker"], "review": ["big"]},
                        "stories": {"repository": str(self.showcase_repo), "base_branch": "master",
                                    "worktree_root": str(base / "wt-storybook"), "mount": "deps/kit", "commands": ["npm --prefix ../.. run build"],
                                    "setup": [["sh", "-c", "echo {kit} > deps/setup.txt"]]}}
        self.profile_path.write_text(json.dumps(self.profile))
        self.profile = profile.load(self.profile_path)
        for patcher in (patch.dict(os.environ, {"PATH": str(self.fake / "bin") + os.pathsep + os.environ.get("PATH", ""),
                                                "FAKE_DIR": str(self.fake)}),
                        patch("friday.models.environment", return_value={"FAKE_DIR": str(self.fake)}),
                        patch("friday.milestone.finish", return_value=None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        card.create(self.root, "T-1", str(self.profile_path))

    def calls(self, *entries):
        (self.fake / "calls.json").write_text(json.dumps([dict(session=s, answer=a, ops=ops or []) for s, ops, a in entries]))
        (self.fake / "counter").unlink(missing_ok=True)
        for old in self.fake.glob("call-*.json"):
            old.unlink()

    def call(self, index):
        return json.loads((self.fake / ("call-%d.json" % index)).read_text())

    def test_the_kit_worktree_lives_inside_the_showcase_worktree_on_the_same_branch(self):
        kit = Path(workspace.create(self.root, "T-1", self.profile))
        value = card.read(self.root, "T-1")
        showcase = Path(value["stories"]["worktree"])
        self.assertEqual(kit, showcase / "deps" / "kit")
        self.assertEqual(git("branch", "--show-current", cwd=showcase).strip(), "T-1")
        self.assertEqual(git("branch", "--show-current", cwd=kit).strip(), "T-1")
        self.assertEqual((showcase / "deps" / "setup.txt").read_text().strip(), str(kit))
        self.assertEqual(git("status", "--porcelain", cwd=showcase), "")  # the kit and the setup output stay ignored
        self.assertEqual(Path(workspace.create(self.root, "T-1", self.profile)), kit)  # a rerun reuses both
        self.assertEqual(stories.executor_commands(self.profile)[:2], ["git -C ../.. add", "git -C ../.. commit"])
        self.assertIn("npm --prefix ../.. run build", stories.executor_commands(self.profile))

    def test_showcase_that_does_not_ignore_the_kit_is_refused(self):
        (self.showcase_repo / ".gitignore").write_text("node_modules\n")
        git("commit", "-qam", "no ignore", cwd=self.showcase_repo)
        git("push", "-q", "origin", "HEAD:master", cwd=self.showcase_repo)
        with self.assertRaises(FridayError) as error:
            workspace.create(self.root, "T-1", self.profile)
        self.assertIn("does not ignore deps/kit", str(error.exception))

    def test_plan_paths_with_the_prefix_are_checked_in_the_showcase(self):
        kit = workspace.create(self.root, "T-1", self.profile)
        showcase = card.read(self.root, "T-1")["stories"]["worktree"]
        self.assertEqual(plan.validate(PLAN, kit, showcase=showcase), [])
        broken = json.loads(json.dumps(PLAN))
        stage = broken["milestones"][0]["stages"][0]
        stage["files"].append("stories:src/stories/Missing.stories.ts")
        stage["new_files"] = ["stories:src/stories/Input.stories.ts", "stories:../escape.ts"]
        broken["milestones"][0]["files"] += stage["files"] + stage["new_files"]
        errors = " ".join(plan.validate(broken, kit, showcase=showcase))
        self.assertIn("do not exist: stories:src/stories/Missing.stories.ts", errors)
        self.assertIn("already exist: stories:src/stories/Input.stories.ts", errors)
        self.assertIn("unsafe path stories:../escape.ts", errors)
        self.assertIn("has no showcase repository", " ".join(plan.validate(PLAN, kit)))

    def test_a_milestone_commits_in_both_repositories_and_the_review_sees_both(self):
        kit = workspace.create(self.root, "T-1", self.profile)
        showcase = card.read(self.root, "T-1")["stories"]["worktree"]
        self.calls(("p", None, PLAN), ("r", None, {"verdict": "ok", "findings": []}))
        plan.make(self.root, "T-1", self.profile, "Кнопка")
        planner = self.call(0)["argv"]
        self.assertEqual(planner[planner.index("--add-dir") + 1], str(Path(showcase).resolve()))
        self.assertIn("stories:src/stories/Button.stories.ts", self.call(0)["prompt"])  # the planner is told the prefix
        plan.approve(self.root, "T-1")
        story = {"dir": "../..", "write": {"src/stories/Button.stories.ts": "export default { title: 'Button' };\n"},
                 "commit": ["T-1 M1.1: история Button"]}
        self.calls(("e1", [story], DONE), ("rev", None, PASS))
        with patch("friday.final.browser", return_value=None):
            milestone.run(self.root, "T-1", self.profile, "M1")
        executor, reviewer = self.call(0), self.call(1)
        argv = executor["argv"]
        self.assertEqual(argv[argv.index("--add-dir") + 1], str(Path(showcase).resolve()))
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertEqual(settings["sandbox"]["filesystem"]["allowWrite"], [str(Path(showcase).resolve())])
        self.assertIn("Bash(git -C ../.. commit:*)", settings["permissions"]["allow"])
        self.assertIn("Bash(npm --prefix ../.. run build:*)", settings["permissions"]["allow"])
        self.assertIn("git -C ../.. commit", executor["prompt"])
        self.assertIn("Diff of the showcase repository", reviewer["prompt"])
        self.assertIn("+export default { title: 'Button' };", reviewer["prompt"])
        self.assertIn("--add-dir", reviewer["argv"])
        done = card.read(self.root, "T-1")["milestones"][0]
        self.assertEqual(done["state"], "done")  # a commit in the showcase alone is progress
        self.assertTrue(done["stages"][0]["showcase_commit"])
        self.assertEqual(done["outside_files"], [])
        text = final.description(self.root, "T-1")
        self.assertIn("Вторая ветка: `T-1`", text)

    def test_runner_passes_extra_directories_to_both_drivers(self):
        logs = Path(self.directory.name) / "logs"
        spec = runner.Spec(driver="claude", model="m", prompt="p", cwd=self.directory.name, log_dir=str(logs),
                           add_dirs=["/tmp/a"], commands=["npm test"], write=True)
        argv = runner.Claude(spec).command(self.directory.name)
        self.assertEqual(argv[argv.index("--add-dir") + 1], str(Path("/tmp/a").resolve()))
        self.assertEqual(json.loads(argv[argv.index("--settings") + 1])["sandbox"]["filesystem"]["allowWrite"],
                         [str(Path("/tmp/a").resolve())])
        fake = self.fake / "bin" / "codex"
        fake.write_text("")
        runtime = Path(self.directory.name) / "runtime"
        runtime.mkdir()
        with patch("friday.runner.shutil.which", return_value=str(fake)), patch("friday.runner.codex_policy.verify_environment"):
            argv = runner.Codex(runner.Spec(**dict(spec.__dict__, driver="codex"))).command(str(runtime))
        self.assertEqual(argv[argv.index("--add-dir") + 1], str(Path("/tmp/a").resolve()))

    def test_stories_settings_are_checked(self):
        for value in ({"repository": "r"}, {"repository": "r", "base_branch": "m", "worktree_root": "w", "mount": "../x"},
                      {"repository": "r", "base_branch": "m", "worktree_root": "w", "setup": ["sh"]},
                      {"repository": "r", "base_branch": "m", "worktree_root": "w", "other": 1}):
            with self.subTest(value=value), self.assertRaises(FridayError):
                stories.validate(value)
        stories.validate({"repository": "r", "base_branch": "m", "worktree_root": "w", "mount": "deps/kit", "image": "sb:local"})
        with self.assertRaises(FridayError):  # no default mount
            stories.validate({"repository": "r", "base_branch": "m", "worktree_root": "w"})



class ThemeTests(unittest.TestCase):
    PROFILE = {"stories": {"repository": "r", "base_branch": "m", "worktree_root": "w", "mount": "deps/kit",
                           "themes": ["light", "dark"], "theme_query": "globals=theme:{theme}"}}

    def test_browser_text_names_each_theme_and_state_comparison(self):
        text = stories.browser_text(self.PROFILE)
        self.assertIn("&globals=theme:dark", text)
        self.assertIn("&globals=theme:light", text)
        self.assertIn("darker or lower in contrast than rest", text)
        self.assertIn("[dark]", text)
        self.assertEqual(stories.browser_text({"stories": dict(self.PROFILE["stories"], themes=[])}), "")
        custom = {"stories": dict(self.PROFILE["stories"], theme_query="theme={theme}")}
        self.assertIn("&theme=dark", stories.browser_text(custom))

    def test_results_per_theme_and_validation(self):
        results = [{"criterion": "[light] hover", "status": "pass"}, {"criterion": "[dark] hover", "status": "fail"},
                   {"criterion": "[dark] focus", "status": "pass"}]
        self.assertEqual(stories.theme_results(results, self.PROFILE), {"light": "pass", "dark": "fail"})
        self.assertEqual(stories.theme_results(results[:1], self.PROFILE), {"light": "pass", "dark": "unverifiable"})
        for bad in ({"themes": "dark"}, {"themes": ["Dark Mode"]}, {"theme_query": "theme=dark"}, {"theme_query": "a&b={theme}"},
                    {"theme_query": None}):
            with self.assertRaises(FridayError):
                stories.validate(dict(self.PROFILE["stories"], **bad))


if __name__ == "__main__":
    unittest.main()
