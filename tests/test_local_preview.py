import os
import subprocess
import tempfile
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

from friday import browser_session, local_preview
from friday.errors import FridayError


class LocalPreviewTests(unittest.TestCase):
    def test_local_browser_rejects_borrowed_sessions_and_external_stage(self):
        for config in ({'mode': 'local', 'stage_origin': 'https://example.stage.invalid'},
                       {'mode': 'local', 'auth': {'storage_key': 'token'}}):
            with self.subTest(config=config), self.assertRaises(FridayError):
                browser_session.configuration(config, 'http://fixture.localhost:1234/')
        self.assertEqual(browser_session.configuration({'mode': 'local'}, 'http://fixture.localhost:1234/'),
                         ('http://fixture.localhost:1234', None))

    def test_preview_rejects_links_before_creating_a_container(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / 'link').symlink_to('/outside')
            def git(*args):
                subprocess.run(['git', '-C', str(repo), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            git('init', '-q')
            git('add', '-A')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'initial')
            selected = {'gate_image': 'image', 'browser': {'mode': 'local'},
                        'preview': {'backend': 'docker-local', 'port': 5173, 'command': ['python3']}}
            with patch('friday.local_preview.gate.image_id', return_value='image'), \
                 patch('friday.local_preview.isolated_preview.docker') as docker, self.assertRaises(FridayError):
                with local_preview.open_preview(selected, str(repo)):
                    pass
            docker.assert_not_called()

    @unittest.skipUnless(os.environ.get('FRIDAY_DOCKER_TEST') == '1', 'requires local friday-test:local image')
    def test_networkless_preview_forwards_synthetic_requests_and_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'repo'
            repo.mkdir()
            (repo / 'server.py').write_text(
                'from http.server import BaseHTTPRequestHandler, HTTPServer\n'
                'class Handler(BaseHTTPRequestHandler):\n'
                ' def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ready")\n'
                ' def do_POST(self):\n'
                '  data=self.rfile.read(int(self.headers["Content-Length"]))\n'
                '  self.send_response(200); self.send_header("Set-Cookie","demo=synthetic; Path=/"); self.end_headers(); self.wfile.write(data)\n'
                'HTTPServer(("127.0.0.1",5173),Handler).serve_forever()\n')
            def git(*args):
                subprocess.run(['git', '-C', str(repo), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            git('init', '-q')
            git('add', '-A')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'initial')
            selected = {'repository': str(repo), 'gate_image': 'friday-test:local', 'browser': {'mode': 'local'},
                        'preview': {'backend': 'docker-local', 'port': 5173,
                                    'command': ['python3', '/workspace/server.py']}}
            with local_preview.open_preview(selected, str(repo)) as url:
                port = urllib.parse.urlsplit(url).port
                endpoint = 'http://127.0.0.1:' + str(port) + '/'
                self.assertEqual(urllib.request.urlopen(endpoint, timeout=5).read(), b'ready')
                request = urllib.request.Request(endpoint, data=b'synthetic', method='POST')
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(response.read(), b'synthetic')
                    self.assertEqual(response.headers['Set-Cookie'], 'demo=synthetic; Path=/')
            with self.assertRaises(OSError):
                urllib.request.urlopen(endpoint, timeout=2)
