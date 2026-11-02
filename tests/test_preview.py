import base64
import json
import unittest
from unittest.mock import patch

from friday import isolated_preview, preview
from friday.errors import FridayError


class PreviewTests(unittest.TestCase):
    def test_readiness_retries_until_successful_html_response(self):
        def response(status, content_type, body):
            return json.dumps({'status': status, 'type': content_type, 'data': base64.b64encode(body).decode()}).encode()
        values = [b'not-json', response(404, 'text/html', b'<html>not found</html>'),
                  response(500, 'text/html', b'<html>error</html>'),
                  response(200, 'application/json', b'<html></html>'),
                  response(200, 'text/html', b'not html'),
                  response(200, 'text/html; charset=utf-8', b'<!doctype html><html></html>')]
        with patch('friday.isolated_preview.docker', side_effect=values) as docker, \
             patch('friday.isolated_preview.time.sleep'):
            isolated_preview.wait_for_vite('synthetic')
        self.assertEqual(docker.call_count, len(values))
        self.assertEqual(docker.call_args.args[-1], '/')

    def test_readiness_fails_closed_after_bounded_retries(self):
        cases = [b'{}', b'[]', b'{"status":200,"type":"text/html","data":"%%%"}',
                 json.dumps({'status': 500, 'type': 'text/html', 'data': 'PGh0bWw+'}).encode()]
        for value in cases:
            with self.subTest(value=value), patch('friday.isolated_preview.docker', return_value=value) as docker, \
                 patch('friday.isolated_preview.time.sleep'), self.assertRaises(FridayError) as error:
                isolated_preview.wait_for_vite('synthetic')
            self.assertEqual(error.exception.code, 'preview_unavailable')
            self.assertEqual(docker.call_count, 40)

    def test_standalone_and_unrestricted_launchers_stop_before_execution(self):
        with patch('subprocess.run') as process:
            with self.assertRaises(FridayError):
                preview.start('missing', 'T-1')
            with self.assertRaises(FridayError):
                with isolated_preview.open_preview({'preview': {'command': ['untrusted']}}, 'missing'):
                    pass
            process.assert_not_called()
