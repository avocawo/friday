import json
import tempfile
import unittest
from pathlib import Path

from friday import profile
from friday.errors import FridayError


class ProfileTests(unittest.TestCase):
    def test_example_profile_publishes_nothing_and_inherits_the_model_catalog(self):
        from friday import models
        example = Path(__file__).resolve().parents[1] / "examples" / "project-profile.json"
        selected = profile.load(example)
        self.assertEqual(selected["name"], "example")
        self.assertTrue(selected["engineering_policy"])
        self.assertEqual(selected["delivery"], {"transport": "none"})
        for key in ("tracker", "gitlab", "github"):
            self.assertNotIn(key, selected)
        self.assertEqual(models.cast(selected)[0]["plan"]["name"], "opus-xhigh")

    def test_home_relative_paths_are_expanded(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "p.json"
            source.write_text(json.dumps({"name": "p", "repository": "~/code/app", "worktree_root": "~/code/app-worktrees"}))
            selected = profile.load(source)
        self.assertEqual(selected["repository"], str(Path.home().resolve() / "code" / "app"))
        self.assertEqual(selected["worktree_root"], str(Path.home().resolve() / "code" / "app-worktrees"))

    def test_every_example_profile_loads_with_a_valid_catalog(self):
        from friday import gate, models
        examples = Path(__file__).resolve().parents[1] / "examples"
        for path in sorted(examples.rglob("*.json")):
            selected = profile.load(path)
            self.assertTrue(models.catalog(selected), path)
            gate.links(selected)
            gate.tmpfs(selected)
            if "roles" in selected:
                self.assertEqual(models.choose(selected, "translate")[0]["tier"], "efficient", path)

    def test_inherited_paths_remain_relative_to_their_own_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            child_dir = root / "child"
            child_dir.mkdir()
            (root / "base.json").write_text(json.dumps({"name": "base", "repository": "repo", "journal_dir": "notes",
                                                         "gitlab": {"base_url": "https://example.invalid", "project": "base"}}))
            child = child_dir / "child.json"
            child.write_text(json.dumps({"extends": "../base.json", "name": "child", "repository": "other",
                                         "gitlab": {"project": "child"}}))
            result = profile.load(child)
            self.assertEqual(result["repository"], str(child_dir / "other"))
            self.assertEqual(result["journal_dir"], str(root / "notes"))
            self.assertEqual(result["gitlab"], {"base_url": "https://example.invalid", "project": "child"})

    def test_inheritance_cycles_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "loop.json"
            path.write_text(json.dumps({"extends": "loop.json"}))
            with self.assertRaises(FridayError) as error:
                profile.load(path)
            self.assertEqual(error.exception.code, "invalid_profile")

    def test_engineering_policy_requires_non_empty_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            for value in ("policy", [""], [1]):
                path.write_text(json.dumps({"name": "test", "repository": ".", "engineering_policy": value}))
                with self.assertRaises(FridayError) as error:
                    profile.load(path)
                self.assertEqual(error.exception.code, "invalid_profile")

    def test_rules_docs_stay_inside_the_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            for value in ("docs/*.md", [""], ["/etc/*"], ["../other/*.md"]):
                path.write_text(json.dumps({"name": "test", "repository": ".", "rules_docs": value}))
                with self.assertRaises(FridayError) as error:
                    profile.load(path)
                self.assertEqual(error.exception.code, "invalid_profile")

    def test_repo_map_settings_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            for extra in ({"repo_map": "tree"}, {"repo_map_budget": 10}, {"repo_map_budget": True},
                          {"repo_map_mcp": True}, {"repo_map": "graphify", "repo_map_mcp": "yes"}):
                path.write_text(json.dumps(dict({"name": "test", "repository": "."}, **extra)))
                with self.assertRaises(FridayError) as error:
                    profile.load(path)
                self.assertEqual(error.exception.code, "invalid_profile")
            path.write_text(json.dumps({"name": "test", "repository": ".", "repo_map": "graphify", "repo_map_mcp": True}))
            self.assertTrue(profile.load(path)["repo_map_mcp"])


if __name__ == "__main__":
    unittest.main()
