import datetime
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, models, runner
from friday.errors import FridayError
from test_runner import FAKE, INIT, RESULT

CATALOG = {
    "opus": {"model": "claude-opus-5-5", "vendor": "anthropic", "driver": "claude", "tier": "frontier"},
    "fable": {"model": "claude-fable-5-1", "vendor": "anthropic", "driver": "claude", "tier": "frontier"},
    "sonnet": {"model": "claude-sonnet-5-5", "vendor": "anthropic", "driver": "claude", "tier": "strong"},
    "astra": {"model": "gpt-6-astra", "vendor": "openai", "driver": "codex", "tier": "frontier"},
    "sol": {"model": "gpt-6.1-sol", "vendor": "openai", "driver": "codex", "tier": "strong",
            "price": {"input": 2.0, "output": 8.0, "cache_read": 0.5}},
    "glm": {"model": "glm-5.3", "vendor": "zai", "driver": "claude", "tier": "strong"},
    "flash": {"model": "glm-5.3-flash", "vendor": "zai", "driver": "claude", "tier": "efficient"},
}


def profile(roles=None, only=None):
    selected = {name: value for name, value in CATALOG.items() if only is None or name in only}
    return {"name": "demo", "repository": "/repo", "models": selected, "roles": roles or {"develop": ["glm"]}}


def names(layout):
    return {role: value["name"] for role, value in layout.items()}


class ModelPolicyTests(unittest.TestCase):
    def test_catalog_rejects_incomplete_models_and_unknown_roles(self):
        for broken in ({"models": {"x": {"model": "m", "vendor": "v", "driver": "claude"}}},
                       {"models": {"x": {"model": "m", "vendor": "v", "driver": "other", "tier": "strong"}}},
                       {"models": CATALOG, "roles": {"develop": ["missing"]}},
                       {"models": CATALOG, "roles": {"cook": ["opus"]}},
                       {"models": {"x": dict(CATALOG["opus"], effort="extreme")}},
                       {"models": {}}):
            with self.subTest(broken=broken), self.assertRaises(FridayError):
                models.catalog(broken)

    def test_cast_puts_a_stronger_or_equal_reviewer_of_another_vendor_on_every_review(self):
        layout, notes = models.cast(profile())
        self.assertEqual(names(layout), {"plan": "opus", "develop": "glm", "plan-review": "astra", "review": "opus",
                                         "final-review": "opus", "blind-code": "fable", "browser": "opus"})
        self.assertEqual(notes, [])
        self.assertEqual(layout["develop"], {"name": "glm", "model": "glm-5.3", "vendor": "zai", "tier": "strong", "driver": "claude"})

    def test_role_lists_are_preferences_and_never_weaken_the_reviewer(self):
        layout, _ = models.cast(profile({"develop": ["opus"], "review": ["sol", "glm"]}))
        self.assertEqual(layout["review"]["name"], "astra")
        layout, _ = models.cast(profile({"develop": ["glm"], "review": ["sol"]}))
        self.assertEqual(layout["review"]["name"], "opus")  # code is reviewed only by frontier models
        layout, _ = models.cast(profile({"develop": ["glm"], "browser": ["sonnet"]}))
        self.assertEqual(layout["browser"]["name"], "sonnet")  # the browser check only has to match its author

    def test_same_vendor_is_allowed_with_a_note_when_no_other_vendor_fits(self):
        layout, notes = models.cast(profile({"develop": ["sonnet"]}, only=("opus", "fable", "sonnet")))
        self.assertEqual(layout["review"]["vendor"], "anthropic")
        self.assertTrue(any("другой вендор недоступен" in note for note in notes))

    def test_final_blind_check_needs_a_frontier_model_other_than_the_author(self):
        with self.assertRaises(FridayError) as error:
            models.cast(profile({"develop": ["glm"]}, only=("sonnet", "sol", "glm")))
        self.assertEqual(error.exception.code, "cast_impossible")
        layout, _ = models.cast(profile({"develop": ["opus"]}, only=("opus", "fable")))
        self.assertEqual(layout["blind-code"]["name"], "fable")

    def test_final_blind_check_avoids_the_plan_author_when_possible(self):
        layout, notes = models.cast(profile({"plan": ["opus"], "develop": ["glm"], "blind-code": ["opus"]}))
        self.assertEqual(layout["blind-code"]["name"], "fable")
        self.assertEqual(notes, [])
        layout, notes = models.cast(profile({"plan": ["opus"], "develop": ["sonnet"]}, only=("opus", "sonnet", "glm")))
        self.assertEqual(layout["blind-code"]["name"], "opus")
        self.assertTrue(any("автора плана" in note for note in notes))

    def test_example_catalog_gives_the_agreed_layout(self):
        from friday import profile as profiles
        selected = profiles.load(Path(__file__).resolve().parents[1] / "examples" / "models.json")
        layout, notes = models.cast(selected)
        self.assertEqual(names(layout), {"plan": "fable", "plan-review": "astra-high", "develop": "glm-high", "review": "opus-high",
                                         "final-review": "opus-xhigh", "blind-code": "astra-high", "browser": "sonnet"})
        self.assertEqual(notes, [])
        layout, notes = models.cast(selected, available=lambda vendor: vendor != "openai")
        self.assertEqual((layout["plan"]["name"], layout["plan-review"]["name"], layout["blind-code"]["name"]),
                         ("opus-xhigh", "fable", "opus-high"))
        chosen, _ = models.executor(selected, layout, failures=2)
        self.assertEqual((chosen["name"], chosen["effort"]), ("opus-high", "high"))

    def test_stronger_frontier_work_is_never_reviewed_by_a_weaker_frontier_model(self):
        catalog = dict(CATALOG, fable=dict(CATALOG["fable"], strength=1))
        selected = {"name": "demo", "repository": "/repo", "models": catalog, "roles": {"plan": ["fable"], "plan-review": ["opus"]}}
        layout, _ = models.cast(selected, available=lambda vendor: vendor != "openai")
        self.assertEqual((layout["plan"]["name"], layout["plan-review"]["name"]), ("opus", "fable"))

    def test_glm_never_reviews_code_and_a_model_does_not_review_itself_if_avoidable(self):
        selected = profile({"develop": ["glm"]})
        layout, _ = models.cast(selected)
        sonnet = models.public(dict(CATALOG["sonnet"], name="sonnet"))
        chosen, _ = models.reviewer(selected, layout, "review", sonnet, available=lambda vendor: vendor != "openai")
        self.assertEqual(chosen["tier"], "frontier")
        opus = models.public(dict(CATALOG["opus"], name="opus"))
        chosen, note = models.reviewer(selected, layout, "review", opus, available=lambda vendor: vendor != "openai")
        self.assertEqual(chosen["name"], "fable")
        self.assertIn("того же вендора", note)
        scarce = dict(selected, models=dict(CATALOG, fable=dict(CATALOG["fable"], scarce=True)),
                      roles={"plan": ["fable"], "develop": ["glm"]})
        layout, _ = models.cast(scarce, available=lambda vendor: vendor != "openai")
        self.assertEqual(layout["plan"]["name"], "fable")
        self.assertNotEqual(layout["review"]["name"], "fable")  # scarce models are used once when others fit
        chosen, note = models.reviewer(scarce, layout, "review", opus, available=lambda vendor: vendor != "openai")
        self.assertEqual(chosen["name"], "fable")  # reused rather than letting Opus review itself
        self.assertIn("повторно", note)

    def test_vendors_out_of_quota_are_skipped(self):
        layout, notes = models.cast(profile(), available=lambda vendor: vendor != "openai")
        self.assertEqual(layout["plan-review"]["vendor"], "anthropic")
        self.assertTrue(notes)
        layout, _ = models.cast(profile(), available=lambda vendor: vendor != "zai")
        self.assertEqual(layout["develop"]["name"], "sonnet")

    def test_executor_follows_complexity_failures_and_quota(self):
        selected = profile({"develop": ["glm", "sonnet"]})
        layout, _ = models.cast(selected)
        self.assertEqual(models.executor(selected, layout), (layout["develop"], None))
        chosen, note = models.executor(selected, layout, complexity="complex")
        self.assertEqual((chosen["tier"], "сложная веха" in note), ("frontier", True))
        chosen, note = models.executor(selected, layout, failures=2)
        self.assertEqual(chosen["tier"], "frontier")
        self.assertIn("два провала подряд", note)
        self.assertIn("новая сессия", note)
        flash = models.public(dict(CATALOG["flash"], name="flash"))
        chosen, _ = models.executor(selected, layout, failures=2, previous=flash)
        self.assertEqual(chosen["tier"], "strong")
        chosen, note = models.executor(selected, layout, available=lambda vendor: vendor != "zai")
        self.assertEqual(chosen["name"], "sonnet")
        self.assertIn("квота вендора", note)

    def test_reviewer_is_checked_against_the_actual_author(self):
        selected = profile({"develop": ["glm"], "review": ["sol"]})
        layout, _ = models.cast(selected)
        glm = layout["develop"]
        self.assertEqual(models.reviewer(selected, layout, "review", glm), (layout["review"], None))
        opus = models.public(dict(CATALOG["opus"], name="opus"))
        chosen, note = models.reviewer(selected, layout, "review", opus)
        self.assertEqual(chosen["name"], "astra")
        self.assertIn("заменён", note)

    def test_quota_reset_times_are_parsed_and_respected(self):
        with tempfile.TemporaryDirectory() as root:
            now = time.time()
            message = "You've hit your usage limit. Try again at Nov 4th, 2026 12:10 PM."
            until = models.exhausted(root, "openai", message, now=now)
            self.assertEqual(until, datetime.datetime(2026, 11, 4, 12, 10).timestamp())
            self.assertEqual(models.exhausted(root, "anthropic", "limit", {"resetsAt": now + 50}, now=now), now + 50)
            self.assertEqual(models.exhausted(root, "zai", "429", now=now), now + models.QUOTA_FALLBACK)
            available = models.availability(root, now=now)
            self.assertFalse(available("openai"))
            self.assertTrue(models.availability(root, now=now + 51)("anthropic"))
            self.assertTrue(available("other"))

    def test_price_estimate_and_vendor_environment(self):
        selected = profile()
        sol = {"name": "sol"}
        self.assertAlmostEqual(models.estimate(sol, selected, {"input": 1e6, "output": 1e5, "cache_read": 2e6}), 2.0 + 0.8 + 1.0)
        self.assertIsNone(models.estimate({"name": "glm"}, selected, {"input": 10}))
        self.assertEqual(models.environment({"name": "opus"}, selected), {})
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "zai.env"
            token.write_text("ZAI_API_KEY=synthetic\n")
            token.chmod(0o600)
            selected["models"]["glm"] = dict(CATALOG["glm"], token_file=str(token), token_key="ZAI_API_KEY")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("FRIDAY_ZAI_TOKEN", None)
                env = models.environment({"name": "glm"}, selected)
        self.assertEqual(env, {"ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic", "ANTHROPIC_AUTH_TOKEN": "synthetic"})

    def test_runs_record_quota_and_estimated_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            root, fake = Path(directory) / "home", Path(directory) / "fake"
            (fake / "bin").mkdir(parents=True)
            for name in ("claude", "codex"):
                (fake / "bin" / name).write_text(FAKE)
                (fake / "bin" / name).chmod(0o755)
            card.create(root, "T-1", "/demo.json")
            selected = profile()
            spec = lambda driver: runner.Spec(driver=driver, model="m", prompt="p", cwd=directory, log_dir="", env={"FAKE_DIR": str(fake)})
            with patch.dict(os.environ, {"PATH": str(fake / "bin") + os.pathsep + os.environ.get("PATH", "")}):
                (fake / "scenario.json").write_text(json.dumps([
                    {"emit": {"type": "thread.started", "thread_id": "t"}},
                    {"emit": {"type": "turn.completed", "usage": {"input_tokens": 1000000, "output_tokens": 0}}}]))
                card.run_agent(root, "T-1", spec("codex"), role="review", entry={"name": "sol", "vendor": "openai", "tier": "strong"},
                               profile=selected)
                usage = card.events(root, "T-1")[-1]["usage"]
                self.assertEqual((usage["cost_usd"], usage["cost_estimated"]), (2.0, True))
                limit = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 4102444800}}
                (fake / "scenario.json").write_text(json.dumps([{"emit": INIT}, {"emit": limit},
                                                                {"emit": dict(RESULT, is_error=True)}, {"exit": 1}]))
                card.run_agent(root, "T-1", spec("claude"), role="develop", entry={"name": "opus", "vendor": "anthropic", "tier": "frontier"},
                               profile=selected)
            self.assertEqual(card.events(root, "T-1")[-1]["quota_until"], 4102444800)
            self.assertFalse(models.availability(root)("anthropic"))


if __name__ == "__main__":
    unittest.main()
