import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

import friday

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_plugins_carry_the_package_version(self):
        for path in ("plugins/friday/.claude-plugin/plugin.json", "plugins/friday/.codex-plugin/plugin.json"):
            self.assertEqual(json.loads((ROOT / path).read_text())["version"], friday.__version__, path)

    def test_package_data_lives_inside_the_package(self):
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn('friday = "friday.cli:main"', pyproject)
        for pattern in re.search(r'friday = \[(.*?)\]', pyproject).group(1).replace('"', "").split(", "):
            self.assertTrue(list((ROOT / "friday").glob(pattern)), pattern)

    def test_module_entry_point_prints_the_version(self):
        result = subprocess.run([sys.executable, "-m", "friday", "--version"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "friday " + friday.__version__))


if __name__ == "__main__":
    unittest.main()
