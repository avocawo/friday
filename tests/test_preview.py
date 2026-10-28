import unittest
from unittest.mock import patch

from friday import isolated_preview, preview
from friday.errors import FridayError


class PreviewTests(unittest.TestCase):
    def test_standalone_and_unrestricted_launchers_stop_before_execution(self):
        with patch('subprocess.run') as process:
            with self.assertRaises(FridayError):
                preview.start('missing', 'T-1')
            with self.assertRaises(FridayError):
                with isolated_preview.open_preview({'preview': {'command': ['untrusted']}}, 'missing'):
                    pass
            process.assert_not_called()
