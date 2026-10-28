import tempfile
import unittest
from pathlib import Path

from friday import decisions, stages, state


class DecisionTests(unittest.TestCase):
    def test_revised_decision_reopens_closed_stages_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state.create(root, "T-1", "/unused")
            stages.add(root, "T-1", "one")
            state.update(root, "T-1", lambda card: card["stages"]["one"].update({"status": "closed", "evidence": "old"}))
            first = decisions.set_decision(root, "T-1", "name", "first")
            card = state.read(root, "T-1")
            self.assertEqual(card["stages"]["one"]["status"], "open")
            self.assertEqual(card["stages"]["one"]["evidence"], "old")
            self.assertEqual(first["revision"], 1)
            self.assertEqual(decisions.set_decision(root, "T-1", "name", "first")["revision"], 1)
            self.assertEqual(decisions.set_decision(root, "T-1", "name", "second")["revision"], 2)


if __name__ == "__main__":
    unittest.main()
