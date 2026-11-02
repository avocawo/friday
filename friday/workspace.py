"""Create the task's own branch and worktree from the freshly fetched base branch, and record them on the card."""
import os
import subprocess
from pathlib import Path

from . import card as cards
from . import state
from .errors import FridayError
from .locks import locked


def git(repo, *args, timeout=90):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1")
    try:
        result = subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
                                capture_output=True, text=True, env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FridayError("git_error", str(exc))
    if result.returncode:
        raise FridayError("git_error", result.stderr.strip() or "git " + args[0] + " failed")
    return result.stdout.strip()


def registered(repo, target, branch):
    records = [chunk.split("\0") for chunk in git(repo, "worktree", "list", "--porcelain", "-z").split("\0\0")]
    return any("worktree " + str(target) in record and "branch refs/heads/" + branch in record for record in records)


def create(root, key, profile):
    """Return the worktree path, creating the branch from origin/<base> once; a rerun reuses what exists."""
    repo = profile["repository"]
    base = profile.get("base_branch")
    if not profile.get("worktree_root") or not isinstance(base, str) or not base or base.startswith("-"):
        raise FridayError("invalid_profile", "worktree_root and base_branch are required to prepare a worktree.")
    branch = state.task_branch(profile.get("branch_template", "friday/{key}"), key)
    target = Path(profile["worktree_root"]).resolve() / (branch if profile.get("worktree_layout", "key") == "branch" else key)
    card = cards.read(root, key)
    if card.get("worktree"):
        if card["worktree"] != str(target) or not registered(repo, target, branch):
            raise FridayError("worktree_conflict", "The recorded worktree is unavailable or conflicts with the profile.")
        return card["worktree"]
    with locked(root, "repo:" + str(Path(repo).resolve()), timeout=300):
        if os.path.lexists(target):
            if not registered(repo, target, branch):
                raise FridayError("worktree_conflict", "Worktree path already exists: " + str(target))
            base_commit = git(target, "merge-base", "HEAD", "origin/" + base)
        else:
            git(repo, "check-ref-format", "--branch", branch)
            git(repo, "check-ref-format", "--branch", base)
            git(repo, "fetch", "--no-tags", "origin", base)
            base_commit = git(repo, "rev-parse", "FETCH_HEAD^{commit}")
            target.parent.mkdir(parents=True, exist_ok=True)
            git(repo, "worktree", "add", "-b", branch, str(target), base_commit)
    cards.update(root, key, lambda value: value.update(branch=branch, worktree=str(target), base=base_commit))
    cards.record(root, key, "worktree_created", branch=branch, worktree=str(target), base=base_commit)
    return str(target)
