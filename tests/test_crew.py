import unittest

from friday import crew
from friday.errors import FridayError


class CrewTests(unittest.TestCase):
    def setUp(self):
        self.profile = {"agents": {"coder": {"vendor": "openai", "model": "dev", "roles": ["develop"]},
                                    "reviewer": {"vendor": "anthropic", "model": "review", "roles": ["review"]},
                                    "other": {"vendor": "openai", "model": "different", "roles": ["review"]}},
                        "skills": {"review": {"description": "Review the diff"}}, "max_agents_per_stage": 3}
        self.developer = {"id": "code", "agent": "coder", "role": "develop", "brief": "Implement", "skills": [], "depends_on": []}
        self.reviewer = {"id": "check", "agent": "reviewer", "role": "review", "brief": "Review", "skills": ["review"], "depends_on": ["code"]}

    def test_accepts_configured_agents_and_skills(self):
        self.assertEqual(crew.validate([self.developer, self.reviewer], self.profile), [self.developer, self.reviewer])

    def test_rejects_same_vendor_even_with_a_different_model_name(self):
        self.reviewer["agent"] = "other"
        self.profile["agents"]["other"]["vendor"] = " Codex "
        with self.assertRaises(FridayError) as error:
            crew.validate([self.developer, self.reviewer], self.profile)
        self.assertEqual(error.exception.code, "reviewer_not_independent")

    def test_rejects_unknown_agent_skill_cycle_and_limit(self):
        cases = [dict(self.reviewer, agent="unknown"), dict(self.reviewer, skills=["unknown"]),
                 dict(self.developer, depends_on=["check"])]
        for assignment in cases:
            with self.subTest(assignment=assignment):
                plan = [assignment, self.reviewer] if assignment["id"] == "code" else [self.developer, assignment]
                with self.assertRaises(FridayError):
                    crew.validate(plan, self.profile)
        with self.assertRaises(FridayError):
            crew.validate([self.developer] * 4, self.profile)
