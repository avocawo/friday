"""Stop a running task and archive a finished or abandoned one."""
import os
import signal
import subprocess
import time
from pathlib import Path

from . import card as cards
from . import resources
from .errors import FridayError


def stop(root, key):
    """Ask the process running the task to stop; it finishes the current step's cleanup and marks the task stopped."""
    value = cards.read(root, key)
    pid = value.get("runner_pid")
    if value["state"] != "running" or not isinstance(pid, int) or not cards.alive(pid):
        raise FridayError("not_running", "Task {key} is not running.", key=key)
    os.kill(pid, signal.SIGTERM)
    return pid


def archive(root, key, profile, force=False):
    """Remove the task's worktree and move its card aside so the key can start again; the branch and its commits stay."""
    value = cards.read(root, key)
    if value["state"] == "running" and isinstance(value.get("runner_pid"), int) and cards.alive(value["runner_pid"]):
        raise FridayError("task_busy", "Task {key} is running; stop it first.", key=key)
    worktree = value.get("worktree")
    if worktree and Path(worktree).exists():
        dirty = subprocess.run(["git", "-C", worktree, "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        if dirty and not force:
            raise FridayError("dirty_tree", "The worktree has uncommitted changes; commit them or pass --force to drop them.")
        result = subprocess.run(["git", "-C", profile["repository"], "worktree", "remove", "--force", worktree], capture_output=True, text=True)
        if result.returncode:
            raise FridayError("git_error", result.stderr.strip())
    resources.release_port(root, key)
    target = Path(root) / "archived-cards" / ("%s-%s" % (key, time.strftime("%Y%m%d-%H%M%S")))
    target.parent.mkdir(parents=True, exist_ok=True)
    cards.directory(root, key).rename(target)
    return {"archive": str(target), "branch": value.get("branch")}
