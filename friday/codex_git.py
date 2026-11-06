"""Private Git runtime: sandboxed commits return through a bundle, never shared metadata access."""
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import codex_policy
from .errors import FridayError


def installed_git():
    executable = shutil.which('git')
    if sys.platform == 'darwin' and executable == '/usr/bin/git':
        result = subprocess.run(['/usr/bin/xcrun', '--find', 'git'], capture_output=True, text=True, timeout=10)
        executable = result.stdout.strip() if result.returncode == 0 else None
    if not executable or not Path(executable).is_file():
        raise FridayError('environment_unavailable', 'An installed Git binary is required for Codex.')
    return str(Path(executable).resolve())


def host_git(binary, workspace, *args, index_file=None):
    environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    environment.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0')
    if index_file is not None:
        environment['GIT_INDEX_FILE'] = str(index_file)
    result = subprocess.run([binary, '--no-optional-locks', '-C', str(workspace),
                             '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                             '-c', 'commit.gpgsign=false', *args], env=environment,
                            capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise FridayError('git_error', result.stderr.strip() or 'Git runtime preparation failed.')
    return result.stdout


class Runtime:
    def __init__(self, workspace, directory, logs, write):
        self.workspace, self.directory = Path(workspace).resolve(), Path(directory).resolve()
        self.logs, self.write = Path(logs), write
        self.binary = installed_git()
        self.gitdir = self.directory / 'git'
        self.start = self.host('rev-parse', 'HEAD').strip()
        self.index = Path(self.host('rev-parse', '--git-path', 'index').strip())
        if not self.index.is_absolute():
            self.index = self.workspace / self.index
        self.index_digest = self.digest()
        self.git_identity = self.host('rev-parse', '--absolute-git-dir').strip()
        self.head_file = (Path(self.git_identity) / 'HEAD').read_bytes()
        self.bin = self.directory / 'bin'
        self.bin.mkdir()
        shutil.copy2(self.binary, self.bin / 'git')
        config = self.directory / 'gitconfig'
        config.write_text('[core]\n hooksPath = /dev/null\n fsmonitor = false\n excludesFile = /dev/null\n'
                          '[commit]\n gpgsign = false\n', encoding='utf-8')
        self.environment = {'PATH': str(self.bin) + os.pathsep + os.environ.get('PATH', ''),
                            'GIT_DIR': str(self.gitdir), 'GIT_WORK_TREE': str(self.workspace),
                            'GIT_CONFIG_GLOBAL': str(config), 'GIT_CONFIG_NOSYSTEM': '1',
                            'GIT_EXEC_PATH': str(self.bin), 'GIT_TERMINAL_PROMPT': '0'}
        shell = self.directory / 'shell'
        shell.mkdir()
        path_setting = 'export PATH=' + shlex.quote(self.environment['PATH']) + '\n'
        (shell / '.zshenv').write_text('unsetopt GLOBAL_RCS RCS\n' + path_setting, encoding='utf-8')
        (shell / 'bashenv').write_text(path_setting, encoding='utf-8')
        self.environment.update(ZDOTDIR=str(shell), BASH_ENV=str(shell / 'bashenv'))
        source = self.directory / 'source.bundle'
        self.host('bundle', 'create', str(source), 'HEAD')
        self.host('init', '--bare', '-q', str(self.gitdir))
        self.host('--git-dir=' + str(self.gitdir), 'fetch', '-q', '--no-tags', str(source), 'HEAD')
        self.host('--git-dir=' + str(self.gitdir), 'update-ref', 'refs/heads/friday', self.start)
        self.host('--git-dir=' + str(self.gitdir), 'symbolic-ref', 'HEAD', 'refs/heads/friday')
        self.host('--git-dir=' + str(self.gitdir), 'config', 'core.bare', 'false')
        self.host('--git-dir=' + str(self.gitdir), '--work-tree=' + str(self.workspace), 'read-tree', 'HEAD')
        source.unlink()

    def host(self, *args, index_file=None):
        return host_git(self.binary, self.workspace, *args, index_file=index_file)

    def digest(self):
        return hashlib.sha256(self.index.read_bytes()).hexdigest() if self.index.exists() else None

    def unchanged(self):
        return (self.host('rev-parse', '--absolute-git-dir').strip() == self.git_identity
                and self.host('rev-parse', 'HEAD').strip() == self.start
                and self.digest() == self.index_digest
                and (Path(self.git_identity) / 'HEAD').read_bytes() == self.head_file)

    def sandbox(self, *args, export=None):
        # Every command reading agent-controlled Git config stays inside the same sandbox.
        options = codex_policy.options('review', str(self.directory), git_directory=str(self.gitdir),
                                       export_directory=str(export) if export else None)
        environment = {key: value for key, value in os.environ.items()
                       if key in ('HOME', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SHELL', 'USER')}
        environment.update(self.environment)
        result = subprocess.run([shutil.which('codex'), 'sandbox', '-C', str(self.workspace), '-P', 'friday',
                                 *options, '--', str(self.bin / 'git'), *args], cwd=str(self.workspace),
                                env=environment, capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise FridayError('git_error', result.stderr.strip() or 'Sandboxed Git export failed.')
        return result.stdout

    def finish(self):
        if not self.write:
            return
        head = self.sandbox('rev-parse', '--verify', 'HEAD').strip()
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', head):
            raise FridayError('git_error', 'Invalid commit returned by the Codex Git runtime.')
        if head == self.start:
            return
        self.sandbox('merge-base', '--is-ancestor', self.start, head)
        export = self.directory / 'export'
        export.mkdir()
        bundle = export / 'commits.bundle'
        self.sandbox('bundle', 'create', str(bundle), 'HEAD', '^' + self.start, export=export)
        advanced, owned_lock = False, False
        lock = Path(str(self.index) + '.lock')
        try:
            if not self.unchanged():
                raise FridayError('stale_evidence', 'The source worktree changed while Codex was running.')
            # A bundle contains objects, not executable repository config, hooks or alternates.
            self.host('bundle', 'verify', str(bundle))
            self.host('fetch', '-q', '--no-tags', '--no-write-fetch-head', str(bundle), 'HEAD')
            self.host('merge-base', '--is-ancestor', self.start, head)
            index = export / 'index'
            self.host('read-tree', head, index_file=index)
            descriptor = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            owned_lock = True
            with os.fdopen(descriptor, 'wb') as destination, index.open('rb') as source:
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            if not self.unchanged():
                raise FridayError('stale_evidence', 'The source worktree changed before importing Codex commits.')
            self.host('update-ref', 'HEAD', head, self.start)
            advanced = True
            os.replace(lock, self.index)  # only metadata changes; keep working files and unfinished edits
            owned_lock = False
        except (FridayError, OSError) as exc:
            if advanced:
                try:
                    self.host('update-ref', 'HEAD', self.start, head)
                except FridayError:
                    pass  # never overwrite a newer owner's ref while trying to undo our own update
            self.logs.mkdir(parents=True, exist_ok=True)
            with bundle.open('rb') as source, (self.logs / 'git-recovery.bundle').open('xb') as destination:
                shutil.copyfileobj(source, destination)
            if isinstance(exc, FridayError):
                raise
            raise FridayError('git_error', 'Cannot import sandboxed Git commits: ' + str(exc))
        finally:
            if owned_lock:
                lock.unlink()


def verify(executable):
    """No model/API call: exercise real Git in a linked worktree under both sandbox roles."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix='friday-codex-git-check-') as directory:
        root = Path(directory).resolve()
        source, workspace = root / 'source', root / 'workspace'
        source.mkdir()
        binary = installed_git()
        host_git(binary, source, 'init', '-q')
        (source / 'input.txt').write_text('approved input\n', encoding='utf-8')
        host_git(binary, source, 'add', 'input.txt')
        host_git(binary, source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-qm', 'fixture')
        host_git(binary, source, 'worktree', 'add', '-qb', 'probe', str(workspace))
        (workspace / '.env').write_text('synthetic', encoding='utf-8')
        private = root / 'private'
        private.write_text('synthetic', encoding='utf-8')
        for role in ('develop', 'review'):
            runtime = root / role
            runtime.mkdir()
            git = Runtime(workspace, runtime, root / 'logs', role == 'develop')
            environment = {key: value for key, value in os.environ.items()
                           if key in ('HOME', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SHELL', 'USER')}
            environment.update(git.environment, GIT_AUTHOR_NAME='Fixture', GIT_COMMITTER_NAME='Fixture',
                               GIT_AUTHOR_EMAIL='fixture@example.invalid', GIT_COMMITTER_EMAIL='fixture@example.invalid')
            script = 'set -e; git status --short >/dev/null; git show HEAD:input.txt >/dev/null; '
            if role == 'develop':
                script += ('for n in 1 2 3; do echo "$n" > "step$n.md"; git add "step$n.md"; '
                           'git commit -qm "stage $n"; done; git diff --check; ')
            else:
                script += 'if git add input.txt 2>/dev/null; then exit 31; fi; '
                script += 'if echo bad > input.txt 2>/dev/null; then exit 32; fi; '
            script += ('if /bin/cat .env >/dev/null 2>&1; then exit 33; fi; '
                       'if /bin/cat "$1" >/dev/null 2>&1; then exit 34; fi; '
                       'if /bin/cat "$2" >/dev/null 2>&1; then exit 35; fi')
            shell = ['/bin/zsh', '-lc'] if sys.platform == 'darwin' else ['/bin/sh', '-c']
            result = subprocess.run([executable, 'sandbox', '-C', str(workspace), '-P', 'friday',
                                     *codex_policy.options(role, str(runtime), git_directory=str(git.gitdir)),
                                     '--', *shell, script, 'sh', str(private), str(source / '.git' / 'config')],
                                    cwd=str(workspace), env=environment, capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise FridayError('environment_unavailable', 'Codex Git sandbox check failed: ' + result.stderr.strip())
            git.finish()
        if host_git(binary, workspace, 'log', '-3', '--format=%s').splitlines() != ['stage 3', 'stage 2', 'stage 1']:
            raise FridayError('environment_unavailable', 'Codex Git sandbox did not return the three stage commits.')
