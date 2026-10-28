import json
import tempfile
import unittest
from pathlib import Path

from friday import profile
from friday.errors import FridayError


class ProfileTests(unittest.TestCase):
    def test_example_profile_creates_only_a_local_card(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "project-profile.json"
        selected = profile.load(example)
        self.assertEqual(selected["name"], "example")
        self.assertTrue(selected["engineering_policy"])
        for key in ("tracker", "gitlab", "worktree_root", "autonomous_delivery"):
            self.assertNotIn(key, selected)

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


if __name__ == "__main__":
    unittest.main()
