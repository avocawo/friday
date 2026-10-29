import json
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import project_service
from friday.errors import FridayError


class ProjectServiceTests(unittest.TestCase):
    def test_install_and_uninstall_only_the_matching_project_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / 'profile.json'
            config.write_text(json.dumps({'name': 'test', 'repository': '.', 'autonomous_delivery': {
                'enabled': True, 'transport': 'github', 'target_branch': 'main'}}))
            path = root / 'agent.plist'
            with patch('friday.project_service.sys.platform', 'darwin'), \
                 patch('friday.project_service.agent_path', return_value=path), \
                 patch('friday.project_service.state.home', return_value=root / 'home'), \
                 patch('friday.project_service.doctor.inspect', return_value={'ready': True}), \
                 patch('friday.project_service.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as launch:
                installed = project_service.install(config, 300)
                self.assertEqual(installed['path'], str(path))
                self.assertTrue(path.exists())
                with self.assertRaises(FridayError):
                    project_service.install(config, 300)
                self.assertEqual(project_service.uninstall(config)['removed'], str(path))
                self.assertFalse(path.exists())
                self.assertEqual([call.args[0][1] for call in launch.call_args_list], ['bootstrap', 'bootout'])

    def test_failed_diagnostics_do_not_install_a_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'profile.json'
            config.write_text(json.dumps({'name': 'test', 'repository': '.', 'autonomous_delivery': {
                'enabled': True, 'transport': 'github', 'target_branch': 'main'}}))
            path = root / 'agent.plist'
            with patch('friday.project_service.sys.platform', 'darwin'), \
                 patch('friday.project_service.agent_path', return_value=path), \
                 patch('friday.project_service.doctor.inspect', return_value={'ready': False}):
                with self.assertRaises(FridayError) as error:
                    project_service.install(config)
            self.assertEqual(error.exception.code, 'environment_unavailable')
            self.assertFalse(path.exists())

    def test_uninstall_preserves_unowned_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'profile.json'
            config.write_text(json.dumps({'name': 'test', 'repository': '.'}))
            path = root / 'agent.plist'
            path.write_bytes(plistlib.dumps({'Label': 'foreign', 'ProgramArguments': ['different']}))
            with patch('friday.project_service.sys.platform', 'darwin'), \
                 patch('friday.project_service.agent_path', return_value=path), \
                 patch('friday.project_service.subprocess.run') as launch:
                with self.assertRaises(FridayError) as error:
                    project_service.uninstall(config)
            self.assertEqual(error.exception.code, 'service_conflict')
            self.assertTrue(path.exists())
            launch.assert_not_called()
