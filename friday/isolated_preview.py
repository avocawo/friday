"""Serve a committed Vite snapshot from a container with no network."""
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
from pathlib import Path, PurePosixPath

from . import browser_session, gate
from .errors import FridayError

BRIDGE = r'''import base64,json,sys,urllib.request,urllib.error
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): return None
try:
 request=urllib.request.Request('http://127.0.0.1:5173'+sys.argv[1],headers={'Host':'localhost:5173'})
 try: response=urllib.request.build_opener(NoRedirect).open(request,timeout=30)
 except urllib.error.HTTPError as error: response=error
 with response:
  print(json.dumps({'status':response.status,'type':response.headers.get('Content-Type','application/octet-stream'),'data':base64.b64encode(response.read()).decode()}))
except Exception: sys.exit(1)
'''


def docker(*args, timeout=60):
    try:
        result = subprocess.run(['docker', *args], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise FridayError('preview_unavailable', 'The isolated preview container did not respond.')
    if result.returncode:
        raise FridayError('preview_unavailable', 'The isolated preview container failed.')
    return result.stdout


@contextlib.contextmanager
def open_preview(selected, worktree):
    spec = selected.get('preview', {})
    if not isinstance(spec, dict) or spec.get('backend') != 'docker-vite':
        raise FridayError('isolation_unavailable', 'An isolated docker-vite preview is required.')
    stage = selected.get('browser', {}).get('stage_origin')
    api_path = spec.get('api_path')
    if not isinstance(api_path, str) or not api_path.startswith('/') or '?' in api_path or '#' in api_path:
        raise FridayError('invalid_profile', 'The staging API path must be an absolute path without a query.')
    browser_session.configuration(selected.get('browser'), 'http://probe.localhost:12345/')
    image = gate.image_id(selected.get('gate_image'))
    snapshot_profile = dict(selected, repository=worktree)
    _, _, archive = gate.snapshot(snapshot_profile)
    with tempfile.TemporaryDirectory(prefix='friday-preview-') as directory:
        source = Path(directory) / 'src'
        source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as tar:
            members = []
            for item in tar:
                parts = PurePosixPath(item.name).parts
                if item.issym() or item.islnk() or item.name.startswith('/') or '..' in parts or not (item.isfile() or item.isdir()):
                    raise FridayError('preview_unavailable', 'The preview snapshot contains an unsupported file.')
                if any(part == '.env' or part.startswith('.env.') or part == '.npmrc' for part in parts):
                    continue
                members.append(item)
            tar.extractall(source, members=members)
        (source / 'node_modules').symlink_to('/app/node_modules')
        name = 'friday-preview-' + uuid.uuid4().hex
        server = None
        thread = None
        try:
            docker('run', '-d', '--rm', '--pull=never', '--network', 'none', '--read-only',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                   '--tmpfs', '/tmp:rw,exec,nosuid,nodev,size=512m',
                   '--tmpfs', '/app/node_modules/.vite-temp:rw,exec,nosuid,nodev,size=64m',
                   '--tmpfs', '/app/node_modules/.vite:rw,exec,nosuid,nodev,size=512m',
                   '--mount', 'type=bind,src=' + str(source) + ',dst=/src,readonly', '--name', name,
                   '-w', '/src', '-e', 'VITE_API_APOLLO_URI=' + stage + api_path,
                   '-e', 'VITE_APP_ENV=development', '--entrypoint', 'node', image,
                   '/app/node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5173')
            for _ in range(40):
                try:
                    docker('exec', name, 'python3', '-c', BRIDGE, '/', timeout=5)
                    break
                except FridayError:
                    time.sleep(0.5)
            else:
                raise FridayError('preview_unavailable', 'The isolated Vite server did not start.')
            slots = threading.BoundedSemaphore(8)
            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    parsed = urllib.parse.urlsplit(self.path)
                    if parsed.scheme or parsed.netloc or not parsed.path.startswith('/') or parsed.path.startswith('//'):
                        self.send_error(400)
                        return
                    try:
                        with slots:
                            raw = docker('exec', name, 'python3', '-c', BRIDGE, self.path, timeout=40)
                        result = json.loads(raw)
                        data = base64.b64decode(result['data'], validate=True)
                        self.send_response(result['status'])
                        self.send_header('Content-Type', result['type'])
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    except (FridayError, ValueError, KeyError):
                        self.send_error(502)
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
                docker('stop', '--time', '1', name, timeout=10)
            except FridayError:
                pass
