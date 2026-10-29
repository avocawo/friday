"""Bridge a networkless, disposable local application to one browser origin."""
import base64
import contextlib
import http.server
import io
import json
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

from . import browser_session, gate, isolated_preview
from .errors import FridayError

BRIDGE = r'''import base64,http.client,json,sys
try:
 r=json.load(sys.stdin)
 c=http.client.HTTPConnection('127.0.0.1',r['port'],timeout=30)
 c.request(r['method'],r['path'],base64.b64decode(r['body']),r['headers'])
 response=c.getresponse()
 body=response.read(4194305)
 if len(body)>4194304: raise ValueError('response too large')
 headers=[(k,v) for k,v in response.getheaders() if k.lower() in ('content-type','set-cookie','location','cache-control')]
 print(json.dumps({'status':response.status,'headers':headers,'body':base64.b64encode(body).decode()}))
except Exception: sys.exit(1)
'''


def request(name, payload):
    try:
        result = subprocess.run(['docker', 'exec', '-i', name, 'python3', '-c', BRIDGE],
                                input=json.dumps(payload), text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=40)
        response = json.loads(result.stdout) if result.returncode == 0 else None
        if not isinstance(response, dict):
            raise ValueError('invalid response')
        return response
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise FridayError('preview_unavailable', 'The isolated local preview did not respond.')


@contextlib.contextmanager
def open_preview(selected, worktree):
    spec, browser = selected.get('preview'), selected.get('browser')
    if not isinstance(spec, dict) or not isinstance(browser, dict) or browser.get('mode') != 'local':
        raise FridayError('invalid_profile', 'docker-local requires local browser mode.')
    browser_session.configuration(browser, 'http://probe.localhost:12345/')
    port, command = spec.get('port'), spec.get('command')
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise FridayError('invalid_profile', 'Local preview port must be between 1024 and 65535.')
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg for arg in command):
        raise FridayError('invalid_profile', 'Local preview command must be a non-empty argument array.')
    api_port = spec.get('api_port', port)
    if not isinstance(api_port, int) or isinstance(api_port, bool) or not 1024 <= api_port <= 65535:
        raise FridayError('invalid_profile', 'Local API port must be between 1024 and 65535.')
    image = gate.image_id(selected.get('gate_image'))
    _, _, archive = gate.snapshot(dict(selected, repository=worktree))
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as tar:
        if any(item.issym() or item.islnk() or not (item.isfile() or item.isdir()) for item in tar.getmembers()):
            raise FridayError('preview_unavailable', 'Local preview snapshots cannot contain links or special files.')
    with tempfile.TemporaryDirectory(prefix='friday-local-preview-') as directory:
        gate.extract(archive, directory)
        for path in Path(directory).rglob('*'):
            if path.name == '.env' or path.name.startswith('.env.') or path.name == '.npmrc':
                if path.is_file() or path.is_symlink():
                    path.unlink()
        name = 'friday-local-' + uuid.uuid4().hex
        server = thread = None
        try:
            isolated_preview.docker('run', '-d', '--rm', '--pull=never', '--network', 'none', '--read-only',
                                    '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,exec',
                                    '--tmpfs', '/private/tmp:rw,exec', '--mount',
                                    'type=bind,src=' + directory + ',dst=/workspace,readonly',
                                    '--name', name, '-w', '/workspace', '--entrypoint', command[0], image, *command[1:])
            deadline = time.monotonic() + 120
            while True:
                try:
                    response = request(name, {'port': port, 'method': 'GET', 'path': '/', 'body': '', 'headers': {'Host': 'localhost'}})
                    if response.get('status') != 200:
                        raise FridayError('preview_unavailable', 'The local preview is not ready.')
                    break
                except FridayError:
                    if time.monotonic() >= deadline:
                        raise
                    threading.Event().wait(0.5)
            slots = threading.BoundedSemaphore(8)
            class Handler(http.server.BaseHTTPRequestHandler):
                def forward(self):
                    parsed = urllib.parse.urlsplit(self.path)
                    if parsed.scheme or parsed.netloc or not parsed.path.startswith('/') or parsed.path.startswith('//'):
                        self.send_error(400)
                        return
                    try:
                        size = int(self.headers.get('Content-Length', '0'))
                        if not 0 <= size <= 4194304 or self.headers.get('Transfer-Encoding'):
                            self.send_error(413)
                            return
                        headers = {key: value for key, value in self.headers.items()
                                   if key.lower() in ('host', 'cookie', 'x-csrftoken', 'content-type', 'origin', 'referer', 'accept')}
                        destination = api_port if parsed.path == '/api' or parsed.path.startswith('/api/') else port
                        with slots:
                            response = request(name, {'port': destination, 'method': self.command, 'path': self.path,
                                                      'body': base64.b64encode(self.rfile.read(size)).decode(), 'headers': headers})
                        data = base64.b64decode(response['body'], validate=True)
                        self.send_response(response['status'])
                        for key, value in response['headers']:
                            self.send_header(key, value)
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                    except (FridayError, ValueError, KeyError, BrokenPipeError, ConnectionResetError):
                        self.send_error(502)
                do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = forward
                def log_message(self, *args):
                    pass
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            yield 'http://preview-' + uuid.uuid4().hex + '.localhost:' + str(server.server_port) + '/'
        finally:
            if server:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            try:
                isolated_preview.docker('stop', '--time', '1', name, timeout=10)
            except FridayError:
                pass
