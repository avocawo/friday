import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, status


def event(kind, run, at, **fields):
    return {"type": kind, "run": run, "at": at, **fields}


class AccountingTests(unittest.TestCase):
    def test_legacy_parallel_and_resumed_runs_use_their_own_event_pairs(self):
        events = [
            event("run_started", "a", "2026-11-08T10:00:00+00:00"),
            event("run_started", "b", "2026-11-08T10:01:00+00:00"),
            event("run_finished", "a", "2026-11-08T10:02:00+00:00", role="develop", status="ok"),
            event("run_finished", "b", "2026-11-08T10:04:00+00:00", role="review", status="ok"),
            event("resumed", "", "2026-11-09T10:00:00+00:00"),
            event("run_started", "c", "2026-11-09T10:01:00+00:00"),
            event("run_finished", "c", "2026-11-09T10:02:00+00:00", role="develop", status="ok"),
        ]
        spent = status.totals(events)
        self.assertEqual(spent["seconds"], 360)
        self.assertEqual(spent["by_role"]["develop"]["seconds"], 180)
        self.assertEqual(spent["duration_estimated_runs"], 3)
        self.assertEqual(spent["unmeasured_runs"], 0)

    def test_provider_duration_including_zero_takes_precedence(self):
        events = [event("run_started", "a", "2026-11-08T10:00:00+00:00"),
                  event("run_finished", "a", "2026-11-08T10:02:00+00:00", duration_ms=1250),
                  event("run_finished", "b", "2026-11-08T10:03:00+00:00", duration_ms=0)]
        spent = status.totals(events)
        self.assertEqual(spent["seconds"], 1.25)
        self.assertEqual(spent["duration_estimated_runs"], 0)
        self.assertEqual(spent["unmeasured_runs"], 0)

    def test_new_interrupted_run_without_measurement_is_not_a_legacy_estimate(self):
        events = [event("run_started", "a", "2026-11-08T10:00:00+00:00"),
                  event("run_finished", "a", "2026-11-08T10:02:00+00:00", status="interrupted",
                        execution_started=True)]
        spent = status.totals(events)
        self.assertEqual((spent["seconds"], spent["duration_estimated_runs"], spent["unmeasured_runs"]), (0, 0, 1))

    def test_lost_unmatched_and_invalid_runs_remain_unknown(self):
        events = [event("run_started", "lost", "2026-11-01T10:00:00+00:00"),
                  event("run_finished", "lost", "2026-11-08T10:00:00+00:00", status="lost"),
                  event("run_finished", "orphan", "2026-11-08T10:00:00+00:00"),
                  event("run_started", "invalid", "bad timestamp"),
                  event("run_finished", "invalid", "2026-11-08T10:00:00+00:00"),
                  event("run_started", "backwards", "2026-11-08T10:02:00+00:00"),
                  event("run_finished", "backwards", "2026-11-08T10:00:00+00:00")]
        spent = status.totals(events)
        self.assertEqual(spent["seconds"], 0)
        self.assertEqual(spent["unmeasured_runs"], 4)
        self.assertEqual(spent["by_role"]["?"]["unmeasured_runs"], 4)
        self.assertIn("4 without duration", status.work_time(spent))

    def test_unknown_cost_is_preserved_by_role_and_visible_in_task_output(self):
        events = [event("run_finished", "a", "2026-11-08T10:00:00+00:00", role="develop", duration_ms=1000,
                        usage={"cost_usd": None}),
                  event("run_finished", "b", "2026-11-08T10:00:00+00:00", role="review", duration_ms=1000,
                        usage={"cost_usd": 0.25})]
        spent = status.totals(events)
        self.assertEqual(spent["by_role"]["develop"]["unpriced_runs"], 1)
        self.assertEqual(spent["by_role"]["review"]["unpriced_runs"], 0)
        self.assertIn("unknown", status.money(spent["by_role"]["develop"]))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            card.create(root, "T-1", "/missing/profile.json")
            with patch("friday.card.events", return_value=events):
                text = status.render_task(root, "T-1")
            line = next(line for line in text.splitlines() if line.startswith("  development:"))
            self.assertIn("1 unpriced", line)
            self.assertNotIn("$0.00", line)


if __name__ == "__main__":
    unittest.main()
