import json
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import mission, state
from friday.errors import FridayError


class MissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / 'repo'
        self.remote = self.root / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(self.remote)], check=True)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('checkout', '-qb', 'T-1')
        (self.repo / 'file').write_text('initial\n')
        self.git('add', 'file'); self.git('commit', '-qm', 'initial')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.config = self.root / 'profile.json'
        self.config.write_text(json.dumps({'name': 'test', 'repository': str(self.repo), 'branch_template': '{key}',
            'gitlab': {'base_url': 'https://gitlab.example', 'project': 'team/repo'},
            'autonomous_delivery': {'enabled': True, 'transport': 'local', 'local_remote': str(self.remote),
                                    'target_branch': 'develop', 'protected_branches': ['master']}}))
        state.create(self.root, 'T-1', str(self.config), ticket={'summary': 'Task'}, mode='auto')
        state.update(self.root, 'T-1', lambda c: c.update(worktree=str(self.repo), branch='T-1',
            stages={'one': {'status': 'closed', 'criteria': ['works']}}, plan_revision=1, plan_base=self.head()))
        self.mr = None
        self.notes = []
        self.creates = 0
        self.report = patch('friday.mission.report.acceptance', side_effect=self.acceptance)
        self.report.start(); self.addCleanup(self.report.stop)
        self.api = patch('friday.mission.integrations.gitlab_get', side_effect=self.get)
        self.api.start(); self.addCleanup(self.api.stop)
        self.creator = patch('friday.mission.create_mr', side_effect=self.create)
        self.creator.start(); self.addCleanup(self.creator.stop)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def head(self):
        return self.git('rev-parse', 'HEAD')

    def acceptance(self, root, key):
        card = state.read(root, key)
        last = next(reversed(card['stages']))
        return {'head': self.head(), 'criteria': [{'stage': last, 'status': 'gate_passed'}]}

    def get(self, config, path):
        if path == 'user':
            return {'id': 7}
        if '/discussions?' in path:
            return [{'notes': self.notes}]
        if '?' in path:
            return [self.mr] if self.mr else []
        if self.mr:
            ref = subprocess.run(['git', '--git-dir', str(self.remote), 'rev-parse', 'refs/heads/T-1'],
                                 check=True, capture_output=True, text=True).stdout.strip()
            return dict(self.mr, sha=ref)
        raise AssertionError(path)

    def create(self, selected, repo, branch, target, title, description):
        self.creates += 1
        self.mr = {'iid': 42, 'source_branch': branch, 'target_branch': target, 'state': 'opened',
                   'source_project_id': 1, 'target_project_id': 1, 'title': 'Draft: ' + title,
                   'description': description, 'sha': self.head()}
        return self.mr

    def test_publishes_local_branch_creates_mr_once_and_resumes(self):
        result = mission.publish(self.root, 'T-1')
        self.assertEqual(result['merge_request'], 42)
        self.assertEqual(self.creates, 1)
        self.assertEqual(mission.remote_head(str(self.repo), 'T-1'), self.head())
        self.assertEqual(mission.publish(self.root, 'T-1')['merge_request'], 42)
        self.assertEqual(self.creates, 1)

    def test_uncertain_mr_creation_reconciles_without_duplicate(self):
        def lost_response(*args):
            self.create(*args)
            raise FridayError('delivery_unknown', 'lost response')
        with patch('friday.mission.create_mr', side_effect=lost_response):
            with self.assertRaises(FridayError):
                mission.publish(self.root, 'T-1')
        self.assertEqual(mission.publish(self.root, 'T-1')['merge_request'], 42)
        self.assertEqual(self.creates, 1)

    def test_refuses_to_adopt_a_foreign_remote_branch(self):
        self.git('push', 'origin', 'HEAD:refs/heads/T-1')
        with self.assertRaises(FridayError) as error:
            mission.publish(self.root, 'T-1')
        self.assertEqual(error.exception.code, 'remote_conflict')
        self.assertEqual(self.creates, 0)

    def test_refuses_push_when_source_already_has_mr_to_protected_target(self):
        self.mr = {'iid': 90, 'source_branch': 'T-1', 'target_branch': 'master'}
        with self.assertRaises(FridayError) as error:
            mission.publish(self.root, 'T-1')
        self.assertEqual(error.exception.code, 'unsafe_branch')
        self.assertIsNone(mission.remote_head(str(self.repo), 'T-1'))

    def test_recovers_when_push_succeeded_but_response_was_lost(self):
        real_command = mission.command
        def interrupted(repo, *args, **kwargs):
            result = real_command(repo, *args, **kwargs)
            if args[0] == 'push':
                raise FridayError('remote_unavailable', 'response lost')
            return result
        with patch('friday.mission.command', side_effect=interrupted):
            with self.assertRaises(FridayError):
                mission.publish(self.root, 'T-1')
        self.assertEqual(mission.remote_head(str(self.repo), 'T-1'), self.head())
        self.assertEqual(mission.publish(self.root, 'T-1')['merge_request'], 42)
        self.assertEqual(self.creates, 1)

    def test_feedback_runs_once_and_pushes_only_after_stage(self):
        mission.publish(self.root, 'T-1')
        self.notes = [{'id': 10, 'body': 'Please improve this', 'author': {'id': 8}, 'system': False, 'resolved': False},
                      {'id': 11, 'body': 'own reply', 'author': {'id': 7}, 'system': False},
                      {'id': 12, 'body': 'already fixed', 'author': {'id': 8}, 'resolved': True}]
        def repair(root, key):
            current = state.read(root, key)
            stage = current['stages'][current['autonomous_delivery']['pending_review']['stage']]
            self.assertEqual(stage['review_base'], self.head())
            self.assertEqual(stage['criteria'], ['works'])
            (self.repo / 'file').write_text('fixed\n')
            self.git('add', 'file'); self.git('commit', '-qm', 'review fix')
            def close(card):
                stage = card['autonomous_delivery']['pending_review']['stage']
                card['stages'][stage]['status'] = 'closed'
            state.update(root, key, close)
            return {'completed': [], 'remaining': []}
        with patch('friday.mission.autopilot.run', side_effect=repair) as run:
            self.assertEqual(mission.poll(self.root, 'T-1')['processed'], 1)
            self.assertEqual(mission.poll(self.root, 'T-1')['processed'], 0)
            run.assert_called_once()
        self.assertEqual(mission.remote_head(str(self.repo), 'T-1'), self.head())
        self.assertEqual(self.creates, 1)

    def test_refuses_changed_remote_and_protected_target(self):
        data = json.loads(self.config.read_text())
        data['autonomous_delivery']['target_branch'] = 'master'
        self.config.write_text(json.dumps(data))
        with self.assertRaises(FridayError) as error:
            mission.publish(self.root, 'T-1')
        self.assertEqual(error.exception.code, 'invalid_profile')
        self.assertIsNone(mission.remote_head(str(self.repo), 'T-1'))

    def test_changed_review_is_not_pushed(self):
        mission.publish(self.root, 'T-1')
        previous = self.head()
        self.notes = [{'id': 10, 'body': 'Fix this', 'author': {'id': 8}, 'system': False}]
        def repair(root, key):
            (self.repo / 'file').write_text('fixed\n')
            self.git('add', 'file'); self.git('commit', '-qm', 'review fix')
            state.update(root, key, lambda c: c['stages'][c['autonomous_delivery']['pending_review']['stage']].update(status='closed'))
            self.notes[0]['body'] = 'A different review'
        with patch('friday.mission.autopilot.run', side_effect=repair):
            with self.assertRaises(FridayError) as error:
                mission.poll(self.root, 'T-1')
        self.assertEqual(error.exception.code, 'stale_review')
        self.assertEqual(mission.remote_head(str(self.repo), 'T-1'), previous)

    def test_changed_ci_or_template_blocks_before_publishing(self):
        settings = {'transport': 'gitlab', 'ci_policy': {'file_sha256': hashlib.sha256(b'known').hexdigest(),
                    'template_project': 'devops/ci', 'template_branch': 'master', 'template_commit': 'a' * 40}}
        selected = {'gitlab': {'base_url': 'https://gitlab.example'}}
        with patch('friday.mission.gate.git', return_value=b'changed'):
            with self.assertRaises(FridayError) as error:
                mission.verify_policy(selected, settings, str(self.repo))
            self.assertEqual(error.exception.code, 'unsafe_ci')
        with patch('friday.mission.gate.git', return_value=b'known'), \
             patch('friday.mission.integrations.gitlab_get', return_value={'commit': {'id': 'b' * 40}}):
            with self.assertRaises(FridayError) as error:
                mission.verify_policy(selected, settings, str(self.repo))
            self.assertEqual(error.exception.code, 'unsafe_ci')


if __name__ == '__main__':
    unittest.main()
