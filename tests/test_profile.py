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
        self.assertEqual(models.cast(selected)[0]["plan"]["name"], "fable")

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

    def test_project_lessons_require_non_empty_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            for value in ("lesson", [""], [" "], [1]):
                path.write_text(json.dumps({"name": "test", "repository": ".", "project_lessons": value}))
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

    def test_execution_limits_reject_invalid_values_at_profile_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            invalid = [
                {"executor_tiers": value} for value in (None, "strong", [], ["other"], ["strong", "strong"], [True])
            ] + [
                {"max_parallel_milestones": value} for value in (None, 0, -1, True, 1.5, "2")
            ] + [
                {"budget": value} for value in (None, [], 5, {"max_model_run": 10},
                                                {"max_model_runs": 0}, {"max_model_runs": True},
                                                {"max_milestone_runs": -1}, {"max_milestone_runs": 2.5})
            ] + [
                {"final_review_roles": value} for value in (None, "final-review", [], ["blind-code"],
                                                            ["final-review", "final-review"],
                                                            ["blind-code", "final-review"], ["review"])
            ] + [
                {"limits": {name: value}} for name in ("stage_files", "stage_lines", "task_milestones", "task_stages")
                for value in (0, -1, True, 2.5, "3")
            ] + [
                {"limits": value} for value in (None, [], 3)
            ]
            for extra in invalid:
                with self.subTest(extra=extra):
                    path.write_text(json.dumps(dict({"name": "test", "repository": "."}, **extra)))
                    with self.assertRaises(FridayError) as error:
                        profile.load(path)
                    self.assertEqual(error.exception.code, "invalid_profile")

    def test_execution_limits_inherit_and_remain_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.json"
            base.write_text(json.dumps({"name": "base", "repository": ".", "executor_tiers": ["strong", "efficient"],
                                        "budget": {"max_model_runs": 20, "max_milestone_runs": 4},
                                        "max_parallel_milestones": 2,
                                        "final_review_roles": ["final-review", "blind-code"]}))
            child = root / "child.json"
            child.write_text(json.dumps({"name": "child", "extends": "base.json",
                                         "budget": {"max_model_runs": 10}, "final_review_roles": ["final-review"]}))
            selected = profile.load(child)
            self.assertEqual(selected["executor_tiers"], ["strong", "efficient"])
            self.assertEqual(selected["budget"], {"max_model_runs": 10, "max_milestone_runs": 4})
            self.assertEqual(selected["max_parallel_milestones"], 2)
            self.assertEqual(selected["final_review_roles"], ["final-review"])
            base.write_text(json.dumps({"name": "base", "repository": ".", "budget": {}}))
            self.assertEqual(profile.load(base)["budget"], {})
            base.write_text(json.dumps({"name": "base", "repository": ".", "limits": {
                "task_milestones": 3, "task_stages": 6, "future_limit": "preserved"}}))
            self.assertEqual(profile.load(base)["limits"], {
                "task_milestones": 3, "task_stages": 6, "future_limit": "preserved"})


    def test_design_settings_allow_only_local_figma_read_tools_and_skill_references(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            for design in ({"figma": {"url": "https://mcp.figma.com/mcp"}}, {"figma": {"tools": ["send_code_connect_mappings"]}},
                           {"figma": {"tools": []}}, {"figma": {"url": 5}}, {"impeccable": {}},
                           {"impeccable": {"skill_dir": "s", "references": 3}}, {"impeccable": {"skill_dir": "s", "references": {"plan": []}}},
                           {"impeccable": {"skill_dir": "s", "references": {"develop": ["../secret.md"]}}}, {"browser": {}}):
                path.write_text(json.dumps({"name": "test", "repository": ".", "design": design}))
                with self.assertRaises(FridayError) as error:
                    profile.load(path)
                self.assertEqual(error.exception.code, "invalid_profile")
            design = {"figma": {"url": "http://127.0.0.1:3845/mcp", "tools": ["get_metadata"]},
                      "impeccable": {"skill_dir": "~/skill", "references": {"review": ["degraded/finish-reviewer.md"]}}}
            path.write_text(json.dumps({"name": "test", "repository": ".", "design": design}))
            self.assertEqual(profile.load(path)["design"], design)


if __name__ == "__main__":
    unittest.main()
