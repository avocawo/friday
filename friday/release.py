"""Prepare a project release locally; publication remains a separate action."""
import re
import os
import subprocess
import uuid
from pathlib import Path

from . import gate, isolation, profile, report, state, worktree
from .errors import FridayError
from .locks import locked


def prepare(root, key, version):
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise FridayError("invalid_version", "Release version must be a stable MAJOR.MINOR.PATCH.")
    isolation.require_release_build()
    card = state.read(root, key)
    selected = profile.load(card["profile"])
    target, branch, spec = card.get("worktree"), card.get("branch"), selected.get("release")
    if not target or not branch or not isinstance(spec, dict):
        raise FridayError("release_unavailable", "Release requires a task worktree and project release configuration.")
    command, cwd, base = spec.get("build"), spec.get("cwd"), spec.get("base_branch")
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg for arg in command) or not isinstance(cwd, str) or not isinstance(base, str):
        raise FridayError("invalid_profile", "Release requires build arguments, cwd and base_branch.")
    cwd = str((Path(selected["path"]).parent / cwd).resolve())
    command = [target if arg == "{worktree}" else arg for arg in command]
    with locked(root, "resource:branch:" + target, timeout=1800), locked(root, "resource:gate:global", timeout=1800):
        latest = state.read(root, key)
        if latest.get("worktree") != target or latest.get("branch") != branch:
            raise FridayError("worktree_conflict", "Task worktree changed before release.")
        worktree.verify(selected["repository"], target, branch)
        if not report.acceptance(root, key)["final_gate_passed"]:
            raise FridayError("gate_required", "A current final gate is required before release preparation.")
        head = gate.git(target, "rev-parse", "HEAD").decode().strip()
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        try:
            remote = subprocess.run(["git", "-C", target, "ls-remote", "--heads", "origin", "refs/heads/" + base],
                                    capture_output=True, text=True, timeout=60, env=env)
            tags = subprocess.run(["git", "-C", target, "ls-remote", "--tags", "origin", "refs/tags/" + version],
                                  capture_output=True, text=True, timeout=60, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FridayError("environment_unavailable", "Cannot read release refs: " + type(exc).__name__)
        if remote.returncode or tags.returncode or not remote.stdout.strip():
            raise FridayError("environment_unavailable", "Cannot read release refs from origin.")
        if tags.stdout.strip():
            raise FridayError("release_conflict", "Release tag already exists on origin.")
        remote_head = remote.stdout.split()[0]
        try:
            result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FridayError("environment_unavailable", "Release build could not finish: " + type(exc).__name__)
        evidence = state.task_path(root, key).parent / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        log = evidence / (uuid.uuid4().hex + "-release.log")
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise FridayError("release_build_failed", "Release build failed; see " + str(log))
        changed = gate.git(target, "diff", "--name-only", "-z", "HEAD")
        untracked = gate.git(target, "ls-files", "--others", "--exclude-standard", "-z")
        paths = sorted({item.decode("utf-8", "surrogateescape") for item in (changed + untracked).split(b"\0") if item})
        if any(not path.startswith("dist/") for path in paths):
            raise FridayError("release_scope", "Build changed files outside dist; inspect the worktree.")
        try:
            latest_remote = subprocess.run(["git", "-C", target, "ls-remote", "--heads", "origin", "refs/heads/" + base],
                                           capture_output=True, text=True, timeout=60, env=env)
            latest_tags = subprocess.run(["git", "-C", target, "ls-remote", "--tags", "origin", "refs/tags/" + version],
                                         capture_output=True, text=True, timeout=60, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FridayError("environment_unavailable", "Cannot recheck release base: " + type(exc).__name__)
        if latest_remote.returncode or latest_tags.returncode or not latest_remote.stdout.strip():
            raise FridayError("environment_unavailable", "Cannot recheck release refs from origin.")
        if latest_tags.stdout.strip():
            raise FridayError("release_conflict", "Release tag appeared on origin during the build.")
        remote_head = latest_remote.stdout.split()[0]
        record = {"version": version, "head": head, "master_head": remote_head, "changed_dist": paths,
                  "ready_for_tag": remote_head == head and not paths, "log": str(log), "at": state.now()}
        def save(current):
            if current.get("worktree") != target:
                raise FridayError("worktree_conflict", "Task worktree changed during release build.")
            current["release"] = record
            current["events"].append({"type": "release_prepared", "version": version, "head": head, "at": state.now()})
        state.update(root, key, save)
        return record
