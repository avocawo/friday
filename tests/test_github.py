import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import github
from friday.errors import FridayError


class GithubTests(unittest.TestCase):
    def test_backlog_is_read_only_and_marks_a_full_page_as_possibly_truncated(self):
        issues = [{"number": n, "title": "Issue", "url": "https://github.com/a/b/issues/1", "labels": []} for n in range(1, 101)]
        with patch("friday.github.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(issues))) as run:
            result = github.backlog({"github": {"repository": "a/b"}})
        self.assertTrue(result["possibly_truncated"])
        self.assertEqual(run.call_args.args[0][:4], ["gh", "issue", "list", "--repo"])
        self.assertNotIn("body", run.call_args.args[0][-1])

    def test_invalid_repository_is_rejected_before_running_gh(self):
        with patch("friday.github.subprocess.run") as run:
            with self.assertRaises(FridayError) as error:
                github.backlog({"github": {"repository": "host/a/b"}})
        self.assertEqual(error.exception.code, "invalid_profile")
        run.assert_not_called()

    def test_selection_excludes_blocked_assigned_existing_and_linked_work(self):
        def issue(number, blocked=0, assigned=0, labels=None):
            return {'number': number, 'title': 'Example', 'issueDependenciesSummary': {'blockedBy': blocked},
                    'assignees': {'totalCount': assigned}, 'labels': {'pageInfo': {'hasNextPage': False},
                    'nodes': [{'name': x} for x in (labels or ['planned', 'implementation', 'p1'])]}}
        issues = {'repository': {'issues': {'pageInfo': {'hasNextPage': False}, 'nodes':
                  [issue(1, blocked=1), issue(2, assigned=1), issue(3), issue(4), issue(5)]}}}
        pulls = {'repository': {'pullRequests': {'pageInfo': {'hasNextPage': False}, 'nodes': [
            {'headRefName': 'other-3', 'title': '', 'body': '', 'closingIssuesReferences':
             {'pageInfo': {'hasNextPage': False}, 'nodes': []}}]}}}
        selected = {'repository': '/synthetic', 'github': {'repository': 'a/b', 'selection': {
            'required_labels': ['planned', 'implementation'], 'priority_labels': ['p1'], 'key_prefix': 'PROJ'}}}
        with tempfile.TemporaryDirectory() as root, patch('friday.github.graphql', side_effect=[issues, pulls]), \
             patch('friday.gate.git', return_value=b'old-4\n'), patch('friday.github.api', return_value={
                 'number': 5, 'state': 'open', 'title': 'Safe task', 'body': 'Synthetic description'}):
            chosen = github.select_issue(selected, root)
        self.assertEqual(chosen['key'], 'PROJ-5')

    def test_issue_with_credential_material_is_never_selected(self):
        issue = {'number': 1, 'title': 'Example', 'issueDependenciesSummary': {'blockedBy': 0},
                 'assignees': {'totalCount': 0}, 'labels': {'pageInfo': {'hasNextPage': False},
                 'nodes': [{'name': x} for x in ['planned', 'p1']]}}
        replies = [{'repository': {'issues': {'pageInfo': {'hasNextPage': False}, 'nodes': [issue]}}},
                   {'repository': {'pullRequests': {'pageInfo': {'hasNextPage': False}, 'nodes': []}}}]
        selected = {'repository': '/synthetic', 'github': {'repository': 'a/b', 'selection': {
            'required_labels': ['planned'], 'priority_labels': ['p1'], 'key_prefix': 'PROJ'}}}
        with tempfile.TemporaryDirectory() as root, patch('friday.github.graphql', side_effect=replies), \
             patch('friday.gate.git', return_value=b''), patch('friday.github.api', return_value={
                 'number': 1, 'state': 'open', 'title': 'Example', 'body': 'ghp_' + 'x' * 30}):
            with self.assertRaises(FridayError) as error:
                github.select_issue(selected, root)
        self.assertEqual(error.exception.code, 'unsafe_issue')

    def test_private_data_filter_detects_common_credential_and_account_shapes(self):
        for value in ('mail user@example.com', 'account 12345678901234567890', 'call +7 (999) 123-45-67',
                      'Authorization: Bearer ' + 'x' * 24):
            with self.subTest(value=value):
                self.assertIsNotNone(github.PRIVATE_DATA.search(value))

    def test_github_create_requires_confirmed_draft_in_the_same_repository(self):
        payload = {'number': 7, 'state': 'open', 'draft': True, 'title': 'Draft: Task', 'body': 'Description',
                   'head': {'ref': 'work', 'sha': 'a' * 40, 'repo': {'id': 4}},
                   'base': {'ref': 'main', 'repo': {'id': 4}}}
        selected = {'github': {'repository': 'a/b'}}
        with patch('friday.github.api', return_value=payload) as api:
            self.assertEqual(github.create_pull(selected, 'work', 'main', 'Task', 'Description')['iid'], 7)
        self.assertTrue(api.call_args.args[2]['draft'])
        payload['base']['repo']['id'] = 5
        with patch('friday.github.api', return_value=payload), self.assertRaises(FridayError):
            github.create_pull(selected, 'work', 'main', 'Task', 'Description')

    def test_reviews_include_owner_comments_but_skip_resolved_threads(self):
        pull = {'iid': 12, 'state': 'opened', 'source_branch': 'work', 'target_branch': 'main', 'sha': 'a' * 40,
                'source_project_id': 1, 'target_project_id': 1, 'draft': True}
        connection = lambda nodes: {'pageInfo': {'hasNextPage': False}, 'nodes': nodes}
        review = {'repository': {'pullRequest': {'reviewThreads': connection([
            {'isResolved': False, 'isOutdated': False, 'comments': connection([{'id': 'A', 'body': 'Fix it'}])},
            {'isResolved': True, 'isOutdated': False, 'comments': connection([{'id': 'B', 'body': 'Done'}])}]),
            'reviews': connection([{'id': 'C', 'body': 'Also check tests', 'state': 'CHANGES_REQUESTED'}]),
            'comments': connection([{'id': 'D', 'body': 'One more'}])}}}
        with patch('friday.github.get_pull', return_value=pull), patch('friday.github.graphql', return_value=review):
            notes = github.review_notes({'github': {'repository': 'a/b'}}, 12, 'work', 'main', 'a' * 40)
        self.assertEqual([item['id'] for item in notes], ['A', 'C', 'D'])

    def test_only_read_requests_are_retried_after_an_unknown_result(self):
        failure = subprocess.CompletedProcess([], 1, '')
        success = subprocess.CompletedProcess([], 0, '{"ok": true}')
        selected = {'github': {'repository': 'a/b'}}
        with patch('friday.github.subprocess.run', side_effect=[failure, success]) as run:
            self.assertEqual(github.api(selected, 'graphql', {'query': 'query { viewer { id } }'}, read_only=True), {'ok': True})
            self.assertEqual(run.call_count, 2)
        with patch('friday.github.subprocess.run', return_value=failure) as run:
            with self.assertRaises(FridayError) as error:
                github.api(selected, 'repos/a/b/pulls', {'draft': True})
            self.assertEqual(error.exception.code, 'delivery_unknown')
            run.assert_called_once()

    def test_delivery_rejects_public_repository_and_changed_ci(self):
        selected = {'github': {'repository': 'a/b', 'require_private': True}}
        settings = {'target_branch': 'main', 'ci_policy': {'workflow_tree': 'a' * 40}}
        with patch('friday.github.api', return_value={'private': False, 'permissions': {'push': True}}):
            with self.assertRaises(FridayError) as error:
                github.check_delivery(selected, settings)
        self.assertEqual(error.exception.code, 'unsafe_remote')
        replies = [{'private': True, 'permissions': {'push': True}}, {'commit': {'sha': 'b' * 40}},
                   {'truncated': False, 'tree': [{'path': '.github/workflows', 'sha': 'c' * 40}]}]
        with patch('friday.github.api', side_effect=replies):
            with self.assertRaises(FridayError) as error:
                github.check_delivery(selected, settings)
        self.assertEqual(error.exception.code, 'unsafe_ci')

    def test_delivery_rejects_an_active_webhook(self):
        selected = {'github': {'repository': 'a/b', 'require_private': True, 'require_no_webhooks': True}}
        settings = {'target_branch': 'main', 'ci_policy': {'workflow_tree': 'a' * 40}}
        with patch('friday.github.api', side_effect=[{'private': True, 'permissions': {'push': True}}, [{'active': True}]]):
            with self.assertRaises(FridayError) as error:
                github.check_delivery(selected, settings)
        self.assertEqual(error.exception.code, 'unsafe_remote')

    def test_unknown_pagination_is_not_treated_as_complete(self):
        with self.assertRaises(FridayError) as error:
            github.has_more({'pageInfo': {'hasNextPage': None}})
        self.assertEqual(error.exception.code, 'github_error')

    def test_rechecks_other_branches_before_publishing(self):
        issue = {'state': 'OPEN', 'issueDependenciesSummary': {'blockedBy': 0}, 'assignees': {'totalCount': 0},
                 'labels': {'pageInfo': {'hasNextPage': False}, 'nodes': [{'name': 'planned'}, {'name': 'p1'}]}}
        selected = {'repository': '/synthetic', 'github': {'repository': 'a/b', 'selection': {
            'required_labels': ['planned'], 'priority_labels': ['p1'], 'key_prefix': 'PROJ'}}}
        with patch('friday.github.graphql', return_value={'repository': {'issue': issue}}), \
             patch('friday.gate.git', return_value=b'own-1\norigin/own-1\nother-1\n'), \
             patch('friday.github.open_pulls', return_value=[]):
            with self.assertRaises(FridayError) as error:
                github.assert_issue_ready(selected, 1, 'own-1')
        self.assertEqual(error.exception.code, 'stale_issue')

    def test_cli_reads_from_local_fake_gh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "gh"
            fake.write_text('#!/bin/sh\nprintf \'[{"number": 12, "title": "Example", "url": "https://github.com/a/b/issues/12", "labels": []}]\'\n')
            fake.chmod(0o755)
            config = root / "profile.json"
            config.write_text(json.dumps({"name": "test", "repository": ".", "github": {"repository": "a/b"}}))
            cli = Path(__file__).resolve().parents[1] / "bin" / "friday"
            result = subprocess.run([sys.executable, str(cli), "long-weekend", "backlog", "--profile", str(config)],
                                    env=dict(os.environ, PATH=str(root), FRIDAY_HOME=str(root / "state")), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["data"]["issues"][0]["number"], 12)
            self.assertEqual(result.stderr, "")
