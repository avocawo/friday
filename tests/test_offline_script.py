import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class OfflineScriptTests(unittest.TestCase):
    def test_default_uses_the_portable_local_test_image(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "docker"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$RECORD_PATH\"\n")
            fake.chmod(0o755)
            record = Path(directory) / "arguments"
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ.get("PATH", ""), RECORD_PATH=str(record))
            env.pop("FRIDAY_TEST_IMAGE", None)
            script = Path(__file__).resolve().parents[1] / "bin" / "test-offline"
            subprocess.run([str(script)], env=env, check=True)
            self.assertIn("friday-test:local", record.read_text().splitlines())

    def test_container_has_no_network_or_host_docker_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "docker"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$RECORD_PATH\"\n")
            fake.chmod(0o755)
            record = Path(directory) / "arguments"
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ.get("PATH", ""),
                       RECORD_PATH=str(record), FRIDAY_TEST_IMAGE="safe-test-image")
            script = Path(__file__).resolve().parents[1] / "bin" / "test-offline"
            subprocess.run([str(script)], env=env, check=True)
            args = record.read_text().splitlines()
            self.assertEqual(args[args.index("--network") + 1], "none")
            self.assertIn("--read-only", args)
            self.assertIn("--pull=never", args)
            self.assertTrue(all("docker.sock" not in arg for arg in args))
            mount = args[args.index("--mount") + 1]
            self.assertTrue(mount.endswith(",dst=/friday,readonly"))
