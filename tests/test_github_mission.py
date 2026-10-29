import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import mission, state
from friday.errors import FridayError


class GitHubMissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo, self.remote = self.root / 'repo', self.root / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(self.remote)], check=True)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('checkout', '-qb', 'T-1')
        (self.repo / 'file').write_text('initial\n')
        self.git('add', 'file')
        self.git('commit', '-qm', 'initial')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.config = self.root / 'profile.json'
        self.config.write_text(json.dumps({'name': 'test', 'repository': str(self.repo), 'branch_template': '{key}',
            'github': {'repository': 'a/b'}, 'autonomous_delivery': {'enabled': True, 'transport': 'github', 'target_branch': 'main'}}))
        state.create(self.root, 'T-1', str(self.config), {'kind': 'github_issue', 'number': 1, 'summary': 'Synthetic'}, 'auto')
        state.update(self.root, 'T-1', lambda c: c.update(worktree=str(self.repo), branch='T-1',
            stages={'one': {'status': 'closed', 'criteria': ['works']}}, plan_revision=1, plan_base=self.head()))
        self.pull, self.notes, self.creates = None, [], 0
        for target, replacement in (
            ('friday.mission.remote_identity', lambda *_: str(self.remote)),
            ('friday.mission.verify_policy', lambda *_: None),
            ('friday.github.assert_issue_ready', lambda *_: None),
            ('friday.mission.report.acceptance', self.acceptance),
            ('friday.github.matching_pulls', self.matching),
            ('friday.github.get_pull', self.get_pull),
            ('friday.github.create_pull', self.create),
            ('friday.github.review_notes', lambda *_: list(self.notes))):
            mocked = patch(target, side_effect=replacement)
            mocked.start()
            self.addCleanup(mocked.stop)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def head(self):
        return self.git('rev-parse', 'HEAD')

    def acceptance(self, root, key):
        last = next(reversed(state.read(root, key)['stages']))
        return {'head': self.head(), 'criteria': [{'stage': last, 'status': 'gate_passed'}]}

    def matching(self, *_):
        return [self.get_pull()] if self.pull else []

    def get_pull(self, *_):
        if not self.pull:
            return None
        result = dict(self.pull)
        result['sha'] = subprocess.run(['git', '--git-dir', str(self.remote), 'rev-parse', 'refs/heads/T-1'],
                                       check=True, capture_output=True, text=True).stdout.strip()
        return result

    def create(self, selected, branch, target, title, description):
        self.creates += 1
        self.pull = {'iid': 42, 'state': 'opened', 'source_branch': branch, 'target_branch': target,
                     'source_project_id': 1, 'target_project_id': 1, 'title': 'Draft: ' + title,
                     'description': description, 'draft': True}
        return self.get_pull()

    def test_draft_and_review_fix_use_local_remote_and_resume_without_duplicate(self):
        self.assertEqual(mission.publish(self.root, 'T-1')['merge_request'], 42)
        self.assertIn('Refs #1', self.pull['description'])
        self.notes = [{'id': 'COMMENT-1', 'body': 'Fix this', 'digest': hashlib.sha256(b'Fix this').hexdigest()}]
        def repair(root, key):
            (self.repo / 'file').write_text('fixed\n')
            self.git('add', 'file')
            self.git('commit', '-qm', 'fix')
            state.update(root, key, lambda c: c['stages'][c['autonomous_delivery']['pending_review']['stage']].update(status='closed'))
            return {'remaining': []}
        with patch('friday.mission.autopilot.run', side_effect=repair) as run:
            self.assertEqual(mission.poll(self.root, 'T-1')['processed'], 1)
            self.assertEqual(mission.poll(self.root, 'T-1')['processed'], 0)
            run.assert_called_once()
        self.assertEqual(self.creates, 1)
        self.assertEqual(mission.remote_head(str(self.repo), 'T-1'), self.head())

    def test_lost_create_response_reconciles_existing_draft(self):
        def lost(*args):
            self.create(*args)
            raise FridayError('delivery_unknown', 'synthetic lost response')
        with patch('friday.github.create_pull', side_effect=lost):
            with self.assertRaises(FridayError):
                mission.publish(self.root, 'T-1')
        self.assertEqual(mission.publish(self.root, 'T-1')['merge_request'], 42)
        self.assertEqual(self.creates, 1)

    def test_changed_review_is_never_pushed(self):
        mission.publish(self.root, 'T-1')
        original = self.head()
        self.notes = [{'id': 'COMMENT-1', 'body': 'Fix this', 'digest': hashlib.sha256(b'Fix this').hexdigest()}]
        def repair(root, key):
            (self.repo / 'file').write_text('fixed\n')
            self.git('add', 'file')
            self.git('commit', '-qm', 'fix')
            state.update(root, key, lambda c: c['stages'][c['autonomous_delivery']['pending_review']['stage']].update(status='closed'))
            self.notes = []
        with patch('friday.mission.autopilot.run', side_effect=repair):
            with self.assertRaises(FridayError) as error:
                mission.poll(self.root, 'T-1')
        self.assertEqual(error.exception.code, 'stale_review')
        self.assertEqual(mission.remote_head(str(self.repo), 'T-1'), original)


class GitHubPolicyTests(unittest.TestCase):
    def test_ci_tree_and_remote_head_are_verified_before_delivery(self):
        tree, head = 'a' * 40, 'b' * 40
        settings = {'transport': 'github', 'target_branch': 'main', 'ci_policy': {'workflow_tree': tree}}
        selected = {'github': {'repository': 'a/b'}}
        with patch('friday.mission.gate.git', return_value=(tree + '\n').encode()), \
             patch('friday.github.check_delivery', return_value=head), \
             patch('friday.mission.command', side_effect=['', head]):
            mission.verify_policy(selected, settings, '/synthetic')
        with patch('friday.mission.gate.git', side_effect=[(tree + '\n').encode(), b'c' * 40]), \
             patch('friday.github.check_delivery', return_value=head), \
             patch('friday.mission.command', side_effect=['', head]):
            with self.assertRaises(FridayError) as error:
                mission.verify_policy(selected, settings, '/synthetic')
        self.assertEqual(error.exception.code, 'unsafe_ci')

    def test_refuses_to_publish_to_a_different_github_remote(self):
        with patch('friday.mission.command', return_value='git@github.com:other/project.git'):
            with self.assertRaises(FridayError) as error:
                mission.remote_identity('/synthetic', {'github': {'repository': 'a/b'}}, {'transport': 'github'})
        self.assertEqual(error.exception.code, 'unsafe_remote')
