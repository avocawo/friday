import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from friday import codex_policy, runner, codex_git
from friday.errors import FridayError


class CodexPolicyTests(unittest.TestCase):
    def test_project_configuration_is_rejected_before_cli_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve() / "workspace"
            (workspace / ".codex").mkdir(parents=True)
            (workspace / ".codex" / "config.toml").write_text("# configured")
            with self.assertRaises(FridayError) as error:
                codex_policy.verify_environment("/does/not/exist", workspace)
            self.assertEqual(error.exception.code, "isolation_unavailable")

    def test_unexpected_mcp_server_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve() / "workspace"
            workspace.mkdir()
            executable = Path(directory) / "codex"
            executable.write_text("#!/bin/sh\nprintf '%s\\n' '[{\"name\":\"unexpected\"}]'\n")
            executable.chmod(0o755)
            with self.assertRaises(FridayError) as error:
                codex_policy.verify_environment(str(executable), workspace)
            self.assertEqual(error.exception.code, "isolation_unavailable")


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('codex'), 'real macOS Codex sandbox required')
class CodexGitIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='friday-git-integration-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository, self.workspace = self.root / 'source', self.root / 'worktree'
        self.repository.mkdir()
        self.git(self.repository, 'init', '-q')
        (self.repository / 'input.md').write_text('approved input\n')
        self.git(self.repository, 'add', 'input.md')
        self.git(self.repository, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-qm', 'fixture')
        self.git(self.repository, 'worktree', 'add', '-qb', 'task', str(self.workspace))
        (self.root / 'outside-secret').write_text('must not be readable')
        (self.workspace / '.env').write_text('must not be readable')

    def git(self, where, *args):
        result = subprocess.run(['git', '-C', str(where), '-c', 'core.hooksPath=/dev/null', *args],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def sandbox(self, driver, runtime, script):
        command = driver.command(str(runtime))
        configs, environment = [], {key: value for key, value in os.environ.items()
                                    if key in runner.BASE_ENV}
        environment.update(driver.spec.env)
        for index, arg in enumerate(command[:-1]):
            if arg != '-c':
                continue
            setting = command[index + 1]
            if setting.startswith(('default_permissions=', 'permissions.friday.')):
                configs += ['-c', setting]
            prefix = 'shell_environment_policy.set.'
            if setting.startswith(prefix):
                name, value = setting[len(prefix):].split('=', 1)
                environment[name] = json.loads(value)
        return subprocess.run([shutil.which('codex'), 'sandbox', '-C', str(self.workspace), '-P', 'friday',
                               *configs, '--', '/bin/zsh', '-lc', script], cwd=str(self.workspace),
                              env=environment, capture_output=True, text=True, timeout=20)

    def driver(self, write):
        return runner.Codex(runner.Spec(driver='codex', model='unused-no-api-call', prompt='',
                            cwd=str(self.workspace), log_dir=str(self.root / 'logs'), write=write,
                            env={'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                                 'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}))

    def test_linked_worktree_three_commits_return_without_opening_external_paths(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        driver = self.driver(True)
        script = ('set -e; git status --short; git show HEAD:input.md; '
                  'for n in 1 2 3; do echo "$n" > "step$n.md"; git add "step$n.md"; '
                  'git commit -qm "stage $n"; done; git diff --check; '
                  'if /bin/cat .env >/dev/null 2>&1; then exit 41; fi; '
                  'if /bin/cat "' + str(self.root / 'outside-secret') + '" >/dev/null 2>&1; then exit 42; fi; '
                  'if /bin/cat "' + str(self.repository / '.git' / 'config') + '" >/dev/null 2>&1; then exit 46; fi; '
                  'if echo bad > "' + str(self.root / 'outside-write') + '" 2>/dev/null; then exit 43; fi')
        result = self.sandbox(driver, runtime, script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        driver.finish()
        self.assertEqual(self.git(self.workspace, 'log', '-3', '--format=%s').splitlines(),
                         ['stage 3', 'stage 2', 'stage 1'])
        self.assertEqual(self.git(self.workspace, 'status', '--short'), '?? .env')
        self.assertEqual(self.git(self.repository, 'log', '-1', '--format=%s'), 'fixture')
        self.assertFalse((self.root / 'outside-write').exists())

    def test_reviewer_reads_git_but_cannot_write_worktree_or_metadata(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        driver = self.driver(False)
        script = ('set -e; git log -1 --format=%s; git show HEAD:input.md; '
                  'if echo bad > input.md 2>/dev/null; then exit 44; fi; '
                  'if git add input.md 2>/dev/null; then exit 45; fi')
        result = self.sandbox(driver, runtime, script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        driver.finish()
        self.assertEqual((self.workspace / 'input.md').read_text(), 'approved input\n')

    def test_concurrent_source_commit_refuses_import_and_keeps_recovery_bundle(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        driver = self.driver(True)
        result = self.sandbox(driver, runtime, 'echo one > actor.md; git add actor.md; git commit -qm actor')
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.workspace / 'owner.md').write_text('concurrent change')
        self.git(self.workspace, 'add', 'owner.md')
        self.git(self.workspace, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-qm', 'owner')
        with self.assertRaises(FridayError) as error:
            driver.finish()
        self.assertEqual(error.exception.code, 'stale_evidence')
        self.assertEqual(self.git(self.workspace, 'log', '-1', '--format=%s'), 'owner')
        self.assertTrue((self.root / 'logs' / 'git-recovery.bundle').is_file())

    def test_doctor_checks_real_git_with_the_sandbox(self):
        codex_git.verify(shutil.which('codex'))

    def test_owner_index_lock_is_preserved(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        driver = self.driver(True)
        result = self.sandbox(driver, runtime, 'echo one > actor.md; git add actor.md; git commit -qm actor')
        self.assertEqual(result.returncode, 0, result.stderr)
        index = Path(self.git(self.workspace, 'rev-parse', '--git-path', 'index'))
        lock = Path(str(index) + '.lock')
        lock.write_text('owner lock')
        with self.assertRaises(FridayError):
            driver.finish()
        self.assertEqual(lock.read_text(), 'owner lock')
        self.assertEqual(self.git(self.workspace, 'log', '-1', '--format=%s'), 'fixture')
