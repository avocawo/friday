import unittest
from pathlib import Path

from friday import guidance


class GuidanceTest(unittest.TestCase):
    def test_carries_policy_lessons_base_and_document_map(self):
        profile = {"engineering_policy": ["Read the architecture index."], "project_lessons": ["No member without a consumer."]}
        card = {"base": "abc123", "doc_map": [{"path": "docs/architecture/13-repository-api.md", "covers": "repository API"}]}

        value = guidance.text(profile, card, commands=True)

        self.assertIn("- Read the architecture index.", value)
        self.assertIn("- No member without a consumer.", value)
        self.assertIn(guidance.PRECEDENT_COMMAND + " abc123 <pattern>", value)
        self.assertIn("- docs/architecture/13-repository-api.md: repository API", value)

    def test_names_the_search_command_only_to_roles_that_run_commands(self):
        self.assertNotIn(guidance.PRECEDENT_COMMAND, guidance.text({}, {"base": "abc123"}))

    def test_precedent_search_keeps_pattern_and_paths_out_of_options(self):
        import subprocess, sys, tempfile
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", directory], check=True)
            Path(directory, "a.txt").write_text("-O\n")
            subprocess.run(["git", "-C", directory, "add", "a.txt"], check=True)
            subprocess.run(["git", "-C", directory, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "a"], check=True)
            base = subprocess.run(["git", "-C", directory, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

            found = subprocess.run([sys.executable, guidance.PRECEDENT_COMMAND, base, "-Otouch pwned", "--", "a.txt"],
                                   cwd=directory, capture_output=True, text=True)

            self.assertFalse(Path(directory, "pwned").exists())
            self.assertNotEqual(found.returncode, 0)
            self.assertEqual(subprocess.run([sys.executable, guidance.PRECEDENT_COMMAND, "not-a-sha", "x"]).returncode, 2)

    def test_is_empty_without_anything_to_carry(self):
        self.assertEqual(guidance.text({}, {"base": None}), "")


if __name__ == "__main__":
    unittest.main()
