import base64
import json
import unittest
from unittest.mock import patch

from friday import isolated_preview
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
            isolated_preview.wait_for_server('synthetic', 5173)
        self.assertEqual(docker.call_count, len(values))
        self.assertEqual(docker.call_args.args[-2:], ('/', '5173'))

    def test_readiness_fails_closed_after_bounded_retries(self):
        cases = [b'{}', b'[]', b'{"status":200,"type":"text/html","data":"%%%"}',
                 json.dumps({'status': 500, 'type': 'text/html', 'data': 'PGh0bWw+'}).encode()]
        for value in cases:
            with self.subTest(value=value), patch('friday.isolated_preview.docker', return_value=value) as docker, \
                 patch('friday.isolated_preview.time.sleep'), self.assertRaises(FridayError) as error:
                isolated_preview.wait_for_server('synthetic', 5173)
            self.assertEqual(error.exception.code, 'preview_unavailable')
            self.assertEqual(docker.call_count, 40)


class ServerTests(unittest.TestCase):
    def test_docker_vite_expands_to_the_generic_server(self):
        run = isolated_preview.server({'backend': 'docker-vite', 'api_path': '/gql'}, 'https://api.stage.example')
        self.assertEqual((run['port'], run['env']['VITE_API_APOLLO_URI'], run['links']),
                         (5173, 'https://api.stage.example/gql', {'node_modules': '/app/node_modules'}))
        self.assertEqual(isolated_preview.browser_spec({'browser': {}, 'preview': {'backend': 'docker-vite'}})['stage_check'],
                         isolated_preview.VITE_STAGE_CHECK)
        self.assertNotIn('stage_check', isolated_preview.browser_spec({'browser': {}, 'preview': {'backend': 'docker-staging'}}))

    def test_any_dev_server_runs_with_the_staging_origin_in_its_environment(self):
        run = isolated_preview.server({'backend': 'docker-staging', 'command': ['python3', '-m', 'app', '--port', '8000'], 'port': 8000,
                                       'env': {'API_URL': '{stage_origin}/api'}, 'links': {'.venv': '/opt/venv'}},
                                      'https://api.staging.example')
        self.assertEqual((run['command'][0], run['env'], run['links']), ('python3', {'API_URL': 'https://api.staging.example/api'},
                                                                          {'.venv': '/opt/venv'}))
        for bad in ({'command': [], 'port': 8000}, {'command': ['x'], 'port': 80}, {'command': ['x'], 'port': 8000, 'env': {'A B': 'x'}},
                    {'command': ['x'], 'port': 8000, 'links': {'../x': '/y'}}):
            with self.assertRaises(FridayError):
                isolated_preview.server(dict(bad, backend='docker-staging'), 'https://api.staging.example')
