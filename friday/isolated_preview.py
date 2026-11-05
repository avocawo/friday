"""Serve a committed Vite or Storybook snapshot from a container with no network."""
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

from . import browser_session, gate, stories
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


def bridge(port):
    return BRIDGE.replace('5173', str(int(port)))


def wait_for_vite(name, port=5173, attempts=40):
    for _ in range(attempts):
        try:
            result = json.loads(docker('exec', name, 'python3', '-c', bridge(port), '/', timeout=5))
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


def extract(archive, destination):
    """Unpack a snapshot without links, special files or local secrets."""
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as tar:
        members = []
        for item in tar:
            parts = PurePosixPath(item.name).parts
            if item.issym() or item.islnk() or item.name.startswith('/') or '..' in parts or not (item.isfile() or item.isdir()):
                raise FridayError('preview_unavailable', 'The preview snapshot contains an unsupported file.')
            if any(part == '.env' or part.startswith('.env.') or part == '.npmrc' for part in parts):
                continue
            members.append(item)
        tar.extractall(destination, members=members)


@contextlib.contextmanager
def served(name, port):
    """Bridge the container's server to one local origin; stops the container when done."""
    server = None
    thread = None
    try:
        slots = threading.BoundedSemaphore(8)
        script = bridge(port)
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.scheme or parsed.netloc or not parsed.path.startswith('/') or parsed.path.startswith('//'):
                    self.send_error(400)
                    return
                try:
                    with slots:
                        raw = docker('exec', name, 'python3', '-c', script, self.path, timeout=40)
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


ISOLATION = ['--rm', '--pull=never', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
             '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,exec,nosuid,nodev,size=512m']


@contextlib.contextmanager
def open_preview(selected, worktree):
    spec = selected.get('preview', {})
    if isinstance(spec, dict) and spec.get('backend') == 'storybook':
        with storybook_preview(selected, worktree) as url:
            yield url
        return
    if isinstance(spec, dict) and spec.get('backend') == 'docker-local':
        from . import local_preview
        with local_preview.open_preview(selected, worktree) as url:
            yield url
        return
    if not isinstance(spec, dict) or spec.get('backend') != 'docker-vite':
        raise FridayError('isolation_unavailable', 'An isolated docker-vite preview is required.')
    _, stage = browser_session.configuration(selected.get('browser'), 'http://probe.localhost:12345/')
    environment = ['-e', 'VITE_APP_ENV=development']
    if stage:  # local browser mode runs the preview without a staging API
        api_path = spec.get('api_path')
        if not isinstance(api_path, str) or not api_path.startswith('/') or '?' in api_path or '#' in api_path:
            raise FridayError('invalid_profile', 'The staging API path must be an absolute path without a query.')
        environment += ['-e', 'VITE_API_APOLLO_URI=' + stage + api_path]
    image = gate.image_id(selected.get('gate_image'))
    _, _, archive = gate.snapshot(dict(selected, repository=worktree))
    with tempfile.TemporaryDirectory(prefix='friday-preview-') as directory:
        source = Path(directory) / 'src'
        extract(archive, source)
        (source / 'node_modules').symlink_to('/app/node_modules')
        name = 'friday-preview-' + uuid.uuid4().hex
        try:
            docker('run', '-d', *ISOLATION,
                   '--tmpfs', '/app/node_modules/.vite-temp:rw,exec,nosuid,nodev,size=64m',
                   '--tmpfs', '/app/node_modules/.vite:rw,exec,nosuid,nodev,size=512m',
                   '--mount', 'type=bind,src=' + str(source) + ',dst=/src,readonly', '--name', name,
                   '-w', '/src', *environment, '--entrypoint', 'node', image,
                   '/app/node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5173')
            wait_for_vite(name)
        except BaseException:
            try:
                docker('stop', '--time', '1', name, timeout=10)
            except FridayError:
                pass
            raise
        with served(name, 5173) as url:
            yield url


def showcase_of(selected, worktree):
    """The showcase worktree that holds the kit's worktree at its mount."""
    mount = PurePosixPath(stories.mount(selected))
    showcase = Path(worktree)
    for _ in mount.parts:
        showcase = showcase.parent
    if (showcase / mount).resolve() != Path(worktree).resolve():
        raise FridayError('preview_unavailable', 'The kit worktree is not inside a showcase worktree.')
    return showcase


@contextlib.contextmanager
def storybook_preview(selected, worktree):
    """The showcase with the kit's sources at its mount, both from their committed snapshots, served by the profile's command.

    The showcase snapshot is mounted read-only at /src and the kit snapshot at /src/<mount>; the container has no network.
    The image (stories.image), the command, its port, environment, writable directories and links into the image all
    come from the profile's preview block."""
    config = stories.settings(selected)
    spec = selected.get('preview') or {}
    if not config:
        raise FridayError('invalid_profile', 'The storybook preview needs stories in the profile.')
    validate_storybook(spec)
    browser_session.configuration(selected.get('browser'), 'http://probe.localhost:12345/')
    mount = stories.mount(selected)
    image = gate.image_id(config.get('image'))
    showcase = showcase_of(selected, worktree)
    _, _, showcase_archive = gate.snapshot(dict(selected, repository=str(showcase)))
    _, _, kit_archive = gate.snapshot(dict(selected, repository=worktree))
    with tempfile.TemporaryDirectory(prefix='friday-storybook-') as directory:
        source = Path(directory) / 'src'
        extract(showcase_archive, source)
        extract(kit_archive, source / mount)
        for link, target in sorted((spec.get('links') or {}).items()):
            (source / link).symlink_to(target)
        name = 'friday-storybook-' + uuid.uuid4().hex
        command = [part.replace('{mount}', mount) for part in spec['command']]
        port = spec['port']
        options = [part for path in spec.get('tmpfs', []) for part in ('--tmpfs', path + ':rw,exec,nosuid,nodev,size=512m')]
        options += [part for key, value in sorted((spec.get('env') or {}).items()) for part in ('-e', '%s=%s' % (key, value))]
        try:
            docker('run', '-d', *ISOLATION, *options,
                   '--mount', 'type=bind,src=' + str(source) + ',dst=/src,readonly', '--name', name, '-w', '/src',
                   '-e', 'HOME=/tmp', '--entrypoint', command[0], image, *command[1:])
            wait_for_vite(name, port, attempts=240)
        except BaseException:
            try:
                docker('stop', '--time', '1', name, timeout=10)
            except FridayError:
                pass
            raise
        with served(name, port) as url:
            yield url


def validate_storybook(spec):
    """The storybook backend's own keys: command (argv, {mount} allowed), port, env, tmpfs paths and links into the image."""
    # Other keys may come from a parent profile's preview of another backend; this backend reads only its own.
    command = spec.get('command')
    if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
        raise FridayError('invalid_profile', 'preview.command is required: the argument array that serves the showcase.')
    port = spec.get('port')
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise FridayError('invalid_profile', 'preview.port is required: the port the command serves on.')
    env = spec.get('env', {})
    if not isinstance(env, dict) or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) or not isinstance(v, str) for k, v in env.items()):
        raise FridayError('invalid_profile', 'preview.env maps variable names to strings.')
    tmpfs = spec.get('tmpfs', [])
    if not isinstance(tmpfs, list) or any(not isinstance(p, str) or not p.startswith('/') or ':' in p or ',' in p or '..' in p.split('/')
                                          for p in tmpfs):
        raise FridayError('invalid_profile', 'preview.tmpfs lists absolute container paths.')
    links = spec.get('links', {})
    if not isinstance(links, dict) or any(not stories.safe_relative(k) or not isinstance(v, str) or not v.startswith('/')
                                          for k, v in links.items()):
        raise FridayError('invalid_profile', 'preview.links maps paths in the snapshot to absolute container paths.')
