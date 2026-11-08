import tempfile
import unittest
from pathlib import Path

from friday import card, workspace


class BranchNamingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name) / "octis"
        workspace.git(self.directory.name, "init", "-q", "-b", "main", str(self.repo))
        workspace.git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                      "commit", "-qm", "init", "--allow-empty")
        self.profile = {"name": "development-policy", "repository": str(self.repo)}

    def test_remote_repository_name_wins_over_profile_and_directory_names(self):
        for remote in ("https://example.com/team/vpn-tools.git", "git@example.com:team/vpn-tools.git",
                       "ssh://git@example.com:2222/team/vpn-tools.git/", "/local/team/vpn-tools.git",
                       "file:///local/team/vpn-tools.git", "git@example.com:vpn-tools.git",
                       "/local/team/vpn-tools/.git", "../vpn-tools/.git", "file:///local/team/vpn-tools/.git"):
            with self.subTest(remote=remote):
                workspace.git(self.repo, "config", "remote.origin.url", remote)
                self.assertEqual(workspace.task_branch(self.profile, "31", {}), "vpn-tools/31")
        self.assertEqual(workspace.task_branch(self.profile, "OCTIS-031", {}), "vpn-tools/OCTIS-031")

    def test_no_origin_uses_repository_name_even_from_a_linked_worktree(self):
        self.assertEqual(workspace.task_branch(self.profile, "31", {}), "octis/31")
        linked = Path(self.directory.name) / "temporary-checkout"
        workspace.git(self.repo, "worktree", "add", "-q", "--detach", str(linked))
        self.profile["repository"] = str(linked)
        self.assertEqual(workspace.task_branch(self.profile, "31", {}), "octis/31")

    def test_explicit_template_keeps_its_existing_meaning(self):
        self.profile["branch_template"] = "custom/{key}"
        self.assertEqual(workspace.task_branch(self.profile, "31", {}), "custom/31")

    def test_repository_name_becomes_a_valid_git_branch_prefix(self):
        workspace.git(self.repo, "config", "remote.origin.url", "https://example.com/team/VPN%20tools.lock.git")
        branch = workspace.task_branch(self.profile, "31", {})
        self.assertEqual(branch, "VPN-tools-lock/31")
        workspace.git(self.repo, "check-ref-format", "--branch", branch)

    def test_recorded_legacy_branch_and_branch_layout_survive_a_default_change(self):
        origin = Path(self.directory.name) / "upstream.git"
        workspace.git(self.directory.name, "clone", "-q", "--bare", str(self.repo), str(origin))
        workspace.git(self.repo, "remote", "add", "origin", str(origin))
        root = Path(self.directory.name) / "state"
        self.profile.update(base_branch="main", worktree_root=str(Path(self.directory.name) / "worktrees"),
                            worktree_layout="branch", branch_template="legacy/{key}")
        card.create(root, "31", "/local/profile.json")
        target = workspace.create(root, "31", self.profile)
        del self.profile["branch_template"]
        self.assertEqual(workspace.create(root, "31", self.profile), target)
        self.assertEqual(card.read(root, "31")["branch"], "legacy/31")
        self.assertEqual(workspace.git(target, "branch", "--show-current"), "legacy/31")


if __name__ == "__main__":
    unittest.main()
