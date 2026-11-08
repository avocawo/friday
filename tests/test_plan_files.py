import tempfile
import unittest
from pathlib import Path

from friday.plan import validate


def stage(name, files=(), new=()):
    return dict(id=name, brief="fixture", steps=[], files=list(files), new_files=list(new),
                estimate_lines=1, criteria=[dict(text="fixture", check="code")])


def milestone(name, stages, dependencies=()):
    return dict(id=name, title="fixture", goal="fixture", complexity="clear", ui=False, parallel=False,
                depends_on=list(dependencies), stages=stages,
                files=list(dict.fromkeys(path for row in stages for path in row["files"] + row["new_files"])))


class PlannedFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def check_plan(self, *milestones, **options):
        return validate(dict(milestones=list(milestones)), self.repo, **options)

    def test_later_stage_can_edit_files_created_in_its_milestone(self):
        files = ["module.py", "test_module.py"]
        self.assertEqual(self.check_plan(milestone("M1", [stage("s1", new=files), stage("s2", files=files)])), [])
        self.assertEqual(list(self.repo.iterdir()), [])

    def test_transitive_dependency_makes_created_files_available(self):
        first = milestone("M1", [stage("s1", new=["module.py"])])
        middle = milestone("M2", [stage("s2")], ["M1"])
        last = milestone("M3", [stage("s3", files=["module.py"])], ["M2"])
        self.assertEqual(self.check_plan(first, middle, last), [])

    def test_independent_milestone_cannot_supply_a_file(self):
        first = milestone("M1", [stage("s1", new=["module.py"])])
        last = milestone("M2", [stage("s2", files=["module.py"])])
        self.assertIn("Stage s2 changes files that do not exist: module.py.", self.check_plan(first, last))

    def test_file_must_exist_before_the_stage_that_edits_it(self):
        cases = ([stage("s1", files=["module.py"]), stage("s2", new=["module.py"])],
                 [stage("s1", files=["module.py"], new=["module.py"])])
        for stages in cases:
            with self.subTest(stages=stages):
                self.assertIn("Stage s1 changes files that do not exist: module.py.",
                              self.check_plan(milestone("M1", stages)))

    def test_planned_creation_cannot_be_repeated(self):
        first = milestone("M1", [stage("s1", new=["module.py"])])
        for dependencies in ([], ["M1"]):
            with self.subTest(dependencies=dependencies):
                last = milestone("M2", [stage("s2", new=["module.py"])], dependencies)
                self.assertIn("Stage s2 creates files that already exist: module.py.", self.check_plan(first, last))
        self.assertIn("Stage s2 creates files that already exist: module.py.", self.check_plan(
            milestone("M1", [stage("s1", new=["module.py"]), stage("s2", new=["module.py"])])))

    def test_equivalent_paths_refer_to_the_same_planned_file(self):
        self.assertEqual(self.check_plan(milestone("M1", [
            stage("s1", new=["module.py"]), stage("s2", files=["./module.py"])])), [])
