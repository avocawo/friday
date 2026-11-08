import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from friday import card, runner, status
from friday.errors import FridayError


class BudgetTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        card.create(self.root, "T-1", "/profiles/demo.json")

    def run_agent(self, milestone="M1", profile=None):
        spec = runner.Spec(driver="claude", model="worker", prompt="work", cwd=str(self.root), log_dir="")
        return card.run_agent(self.root, "T-1", spec, role="develop", milestone=milestone, profile=profile)

    def test_total_budget_survives_resume_and_reopen(self):
        profile = {"budget": {"max_model_runs": 2}}
        with patch("friday.runner.run", return_value=runner.Result("ok")) as agent:
            self.run_agent(profile=profile)
            card.update(self.root, "T-1", lambda value: value.update(state="stopped"))
            self.run_agent(profile=profile)
            with self.assertRaises(FridayError) as error:
                self.run_agent(profile=profile)
        self.assertEqual(error.exception.code, "budget_exhausted")
        self.assertEqual(agent.call_count, 2)
        self.assertEqual(card.read(self.root, "T-1")["current"], {})

    def test_milestone_budget_counts_review_and_other_milestones_separately(self):
        profile = {"budget": {"max_model_runs": 5, "max_milestone_runs": 1}}
        with patch("friday.runner.run", return_value=runner.Result("ok")):
            self.run_agent("M1", profile)
            self.run_agent("M2", profile)
            with self.assertRaises(FridayError):
                self.run_agent("M1", profile)

    def test_existing_history_counts_toward_a_new_limit(self):
        card.record(self.root, "T-1", "run_started", run="old", role="plan", milestone=None)
        card.record(self.root, "T-1", "run_finished", run="old", role="plan", status="ok")
        with patch("friday.runner.run", return_value=runner.Result("ok")) as agent, self.assertRaises(FridayError):
            self.run_agent(profile={"budget": {"max_model_runs": 1}})
        agent.assert_not_called()

    def test_parallel_admission_reserves_the_last_slot_atomically(self):
        profile = {"budget": {"max_model_runs": 1}}
        admitted = threading.Event()
        release = threading.Event()

        def execute(*args, **kwargs):
            admitted.set()
            self.assertTrue(release.wait(5))
            return runner.Result("ok")

        with patch("friday.runner.run", side_effect=execute) as agent, ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.run_agent, "M1", profile)
            try:
                self.assertTrue(admitted.wait(5))
                second = pool.submit(self.run_agent, "M2", profile)
                with self.assertRaises(FridayError) as error:
                    second.result(timeout=5)
                self.assertEqual(error.exception.code, "budget_exhausted")
            finally:
                release.set()
            first.result(timeout=5)
        self.assertEqual(agent.call_count, 1)

    def test_lost_run_preserves_observed_usage_without_inventing_duration(self):
        logs = card.directory(self.root, "T-1") / "runs" / "lost"
        logs.mkdir(parents=True)
        (logs / "status.json").write_text(json.dumps({"state": "ok", "usage": {"input": 75}}))
        current = {"run": "lost", "role": "develop", "milestone": "M1", "model": "worker", "driver": "claude"}
        card.update(self.root, "T-1", lambda value: value.update(current={"lost": current}))
        card.recover(self.root, "T-1")
        event = card.events(self.root, "T-1")[-1]
        self.assertEqual(event["usage"]["input"], 75)
        self.assertIsNone(event.get("duration_ms"))

    def test_interruption_waiting_for_vendor_records_zero_execution_time(self):
        with patch("friday.resources.slot", side_effect=FridayError("cancelled", "Stopped before admission")), \
                patch("friday.runner.run") as agent, self.assertRaises(FridayError):
            self.run_agent()
        agent.assert_not_called()
        event = card.events(self.root, "T-1")[-1]
        self.assertEqual(event.get("duration_ms"), 0)
        self.assertIs(event.get("execution_started"), False)
        spent = status.totals(card.events(self.root, "T-1"))
        self.assertEqual((spent["seconds"], spent["duration_estimated_runs"], spent["unmeasured_runs"]), (0, 0, 0))

    def test_parallel_admission_does_not_recover_a_live_lane_before_status_is_written(self):
        paused = threading.Event()
        release = threading.Event()
        mkdir = Path.mkdir

        def delay_status_directory(path, *args, **kwargs):
            if path.parent.name == "runs" and not paused.is_set():
                paused.set()
                self.assertTrue(release.wait(5))
            return mkdir(path, *args, **kwargs)

        profile = {"budget": {"max_model_runs": 2}}
        with patch("pathlib.Path.mkdir", delay_status_directory), \
                patch("friday.runner.run", return_value=runner.Result("ok")), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.run_agent, "M1", profile)
            try:
                self.assertTrue(paused.wait(5))
                pool.submit(self.run_agent, "M2", profile).result(timeout=5)
            finally:
                release.set()
            first.result(timeout=5)
        finished = [event for event in card.events(self.root, "T-1") if event["type"] == "run_finished"]
        self.assertEqual(sorted((event["milestone"], event["status"]) for event in finished),
                         [("M1", "ok"), ("M2", "ok")])
        self.assertEqual(card.read(self.root, "T-1")["budget_usage"]["model_runs"], 2)

    def test_parallel_recovery_records_a_lost_run_only_once(self):
        logs = card.directory(self.root, "T-1") / "runs" / "old"
        logs.mkdir(parents=True)
        (logs / "status.json").write_text(json.dumps({"state": "ok", "usage": {"input": 75}, "duration_ms": 2500}))
        current = {"run": "old", "role": "develop", "milestone": "M1", "model": "worker", "driver": "claude"}
        card.update(self.root, "T-1", lambda value: value.update(current={"old": current}))
        barrier = threading.Barrier(2)
        lost = card.lost

        def both_observe(*args):
            barrier.wait(timeout=5)
            return lost(*args)

        with patch("friday.card.lost", side_effect=both_observe), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(card.recover, self.root, "T-1")
            second = pool.submit(card.recover, self.root, "T-1")
            first.result(timeout=5)
            second.result(timeout=5)
        finished = [event for event in card.events(self.root, "T-1") if event["type"] == "run_finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual((finished[0]["status"], finished[0]["usage"]["input"], finished[0]["duration_ms"]),
                         ("lost", 75, 2500))
