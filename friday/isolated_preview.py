"""Serve a committed snapshot of a web app that talks to staging, from a container with no network.

preview.backend "docker-staging" runs the profile's dev server command in the gate image:
{"backend": "docker-staging", "command": [...], "port": 5173, "env": {"API_URL": "{stage_origin}/graphql"},
 "links": {"node_modules": "/app/node_modules"}, "tmpfs": ["/app/node_modules/.vite"]}.
"docker-vite" is the same with the values of a Vite + Apollo app (VITE), taking the API path from api_path."""
import base64
import contextlib
import http.server
import io
import json
import re
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
 request=urllib.request.Request('http://127.0.0.1:'+sys.argv[2]+sys.argv[1],headers={'Host':'localhost:'+sys.argv[2]})
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


VITE = {"command": ["node", "/app/node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "5173"], "port": 5173,
        "env": {"VITE_API_APOLLO_URI": "{stage_origin}{api_path}", "VITE_APP_ENV": "development"},
        "links": {"node_modules": "/app/node_modules"},
        "tmpfs": ["/app/node_modules/.vite-temp:rw,exec,nosuid,nodev,size=64m", "/app/node_modules/.vite:rw,exec,nosuid,nodev,size=512m"]}
VITE_STAGE_CHECK = {"path": "/src/app/main.ts", "variable": "VITE_API_APOLLO_URI"}


def browser_spec(selected):
    """The profile's browser settings, with the stage check a docker-vite preview implies when none is given."""
    spec = dict(selected.get("browser") or {})
    if (selected.get("preview") or {}).get("backend") == "docker-vite" and "stage_check" not in spec and spec.get("mode") != "local":
        spec["stage_check"] = VITE_STAGE_CHECK
    return spec


def server(spec, stage):
    """The dev server to run: command, port, environment with {stage_origin} and {api_path} filled, links and tmpfs."""
    if spec.get('backend') == 'docker-vite':
        api_path = spec.get('api_path')
        if not isinstance(api_path, str) or not api_path.startswith('/') or '?' in api_path or '#' in api_path:
            raise FridayError('invalid_profile', 'The staging API path must be an absolute path without a query.')
        spec = dict(VITE, **{key: value for key, value in spec.items() if key in VITE or key == 'api_path'})
    api_path = spec.get('api_path', '')
    command, port, env = spec.get('command'), spec.get('port'), spec.get('env', {})
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg for arg in command):
        raise FridayError('invalid_profile', 'preview.command must be a non-empty argument array.')
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise FridayError('invalid_profile', 'preview.port must be between 1024 and 65535.')
    if not isinstance(env, dict) or any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', k) or
                                         not isinstance(v, str) for k, v in env.items()):
        raise FridayError('invalid_profile', 'preview.env must map variable names to strings.')
    if not isinstance(api_path, str) or any(x in api_path for x in '?#'):
        raise FridayError('invalid_profile', 'The staging API path must be an absolute path without a query.')
    filled = {key: value.replace('{stage_origin}', stage).replace('{api_path}', api_path) for key, value in env.items()}
    links = gate.links({'gate_links': spec.get('links') or {}})
    tmpfs = spec.get('tmpfs', [])
    gate.tmpfs({'gate_tmpfs': [entry.split(':', 1)[0] for entry in tmpfs]} if isinstance(tmpfs, list) else {'gate_tmpfs': tmpfs})
    return {'command': command, 'port': port, 'env': filled, 'links': links, 'tmpfs': tmpfs}


def wait_for_server(name, port):
    for _ in range(40):
        try:
            result = json.loads(docker('exec', name, 'python3', '-c', BRIDGE, '/', str(port), timeout=5))
            if not isinstance(result, dict) or result.get('status') != 200 or not isinstance(result.get('type'), str):
                raise ValueError('Invalid readiness response')
            if result['type'].split(';', 1)[0].strip().lower() != 'text/html':
                raise ValueError('Preview is not HTML')
            data = base64.b64decode(result['data'], validate=True)
            if not re.search(rb'<(?:!doctype\s+html|html)(?:\s|>)', data, re.IGNORECASE):
                raise ValueError('Preview HTML document is missing')
            return
        except (FridayError, ValueError, KeyError, TypeError):
            time.sleep(0.5)
    raise FridayError('preview_unavailable', 'The isolated preview server did not start.')


@contextlib.contextmanager
def open_preview(selected, worktree):
    spec = selected.get('preview', {})
    if isinstance(spec, dict) and spec.get('backend') == 'docker-local':
        from . import local_preview
        with local_preview.open_preview(selected, worktree) as url:
            yield url
        return
    if not isinstance(spec, dict) or spec.get('backend') not in ('docker-staging', 'docker-vite'):
        raise FridayError('isolation_unavailable', 'An isolated preview is required: backend docker-local, docker-staging or docker-vite.')
    _, stage = browser_session.configuration(selected.get('browser'), 'http://probe.localhost:12345/')
    run = server(spec, stage)
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
        for link, target in run['links'].items():
            if (source / link).exists() or (source / link).is_symlink():
                raise FridayError('invalid_profile', 'preview.links: {path} exists in the snapshot.', path=link)
            (source / link).parent.mkdir(parents=True, exist_ok=True)
            (source / link).symlink_to(target)
        name = 'friday-preview-' + uuid.uuid4().hex
        server = None
        thread = None
        try:
            docker('run', '-d', '--rm', '--pull=never', '--network', 'none', '--read-only',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                   '--tmpfs', '/tmp:rw,exec,nosuid,nodev,size=512m',
                   *[option for entry in run['tmpfs'] for option in ('--tmpfs', entry)],
                   '--mount', 'type=bind,src=' + str(source) + ',dst=/src,readonly', '--name', name, '-w', '/src',
                   *[option for key, value in run['env'].items() for option in ('-e', key + '=' + value)],
                   '--entrypoint', run['command'][0], image, *run['command'][1:])
            wait_for_server(name, run['port'])
            slots = threading.BoundedSemaphore(8)
            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    parsed = urllib.parse.urlsplit(self.path)
                    if parsed.scheme or parsed.netloc or not parsed.path.startswith('/') or parsed.path.startswith('//'):
                        self.send_error(400)
                        return
                    try:
                        with slots:
                            raw = docker('exec', name, 'python3', '-c', BRIDGE, self.path, str(run['port']), timeout=40)
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
