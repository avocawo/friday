"""Preview a worktree deletion and bind confirmation to the reviewed contents."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

from . import gate, profile, state
from .errors import FridayError
from .locks import locked


def inspect(root, key, allow_pending=False):
    card = state.read(root, key)
    if card.get("cleanup_intent") and not allow_pending:
        raise FridayError("cleanup_pending", "Retry the confirmed cleanup to resolve its pending result.")
    selected = profile.load(card["profile"])
    worktree = card.get("worktree")
    if not worktree or not card.get("branch"):
        raise FridayError("worktree_required", "Task has no Friday worktree.")
    snapshot_profile = dict(selected, repository=worktree)
    head, _, _ = gate.snapshot(snapshot_profile)
    branch = gate.git(worktree, "branch", "--show-current").decode().strip()
    if branch != card["branch"]:
        raise FridayError("worktree_conflict", "Worktree branch does not match the task.")
    records = gate.git(selected["repository"], "worktree", "list", "--porcelain", "-z").decode("utf-8", "surrogateescape")
    expected = "worktree " + worktree + "\0"
    if expected not in records:
        raise FridayError("worktree_conflict", "Worktree is not registered in the profile repository.")
    ignored = gate.git(worktree, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    files = []
    for raw in sorted(filter(None, ignored.split(b"\0"))):
        name = os.fsdecode(raw)
        path = Path(worktree) / name
        if path.is_symlink():
            data = os.readlink(str(path)).encode("utf-8", "surrogateescape")
            kind = "symlink"
            digest, size = hashlib.sha256(data).hexdigest(), len(data)
        elif path.is_file():
            kind = "file"
            hashed, size = hashlib.sha256(), 0
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    hashed.update(chunk)
                    size += len(chunk)
            digest = hashed.hexdigest()
        else:
            raise FridayError("unsafe_cleanup", "Unsupported ignored path: " + name)
        files.append({"path": name, "kind": kind, "bytes": size, "sha256": digest})
    payload = {"worktree": worktree, "repository": selected["repository"], "head": head, "branch": branch, "ignored": files}
    token = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return dict(payload, confirmation=token)


def remove(root, key, token):
    initial = state.read(root, key)
    worktree = initial.get("worktree")
    if not worktree:
        raise FridayError("worktree_required", "Task has no Friday worktree.")
    with locked(root, "resource:branch:" + worktree, timeout=1800):
        def finish(card):
            if card.get("worktree") == worktree:
                card.pop("worktree", None)
                card.pop("cleanup_intent", None)
                card["events"].append({"type": "worktree_removed", "path": worktree, "at": state.now()})
        current = state.read(root, key)
        pending = current.get("cleanup_intent")
        if pending:
            if pending.get("confirmation") != token or pending.get("worktree") != worktree:
                raise FridayError("stale_approval", "A different cleanup is already pending.")
            registered = gate.git(pending["repository"], "worktree", "list", "--porcelain", "-z").decode("utf-8", "surrogateescape")
            if not os.path.lexists(worktree) and "worktree " + worktree + "\0" not in registered:
                state.update(root, key, finish)
                return {"removed": worktree}
            if not os.path.lexists(worktree):
                raise FridayError("cleanup_pending", "The path is missing but Git still lists it; inspect before retrying.")
        preview = inspect(root, key, allow_pending=bool(pending))
        if token != preview["confirmation"]:
            raise FridayError("stale_approval", "Worktree or ignored contents changed; preview cleanup again.")
        def mark(card):
            card["cleanup_intent"] = {"confirmation": token, "worktree": worktree, "repository": preview["repository"]}
        if not pending:
            state.update(root, key, mark)
        result = subprocess.run(["git", "-C", preview["repository"], "worktree", "remove", worktree],
                                capture_output=True, text=True)
        if result.returncode:
            raise FridayError("cleanup_failed", result.stderr.strip())
        state.update(root, key, finish)
        return {"removed": worktree}


def cancel(root, key):
    initial = state.read(root, key)
    intent = initial.get("cleanup_intent")
    if not intent or not initial.get("worktree"):
        raise FridayError("cleanup_pending", "No pending cleanup to cancel.")
    worktree = initial["worktree"]
    with locked(root, "resource:branch:" + worktree, timeout=1800):
        current = state.read(root, key)
        if current.get("cleanup_intent") != intent or not os.path.isdir(worktree):
            raise FridayError("cleanup_pending", "The pending worktree no longer exists or changed.")
        records = gate.git(intent["repository"], "worktree", "list", "--porcelain", "-z").decode("utf-8", "surrogateescape")
        if "worktree " + worktree + "\0" not in records:
            raise FridayError("cleanup_pending", "Worktree is no longer registered; inspect before cancellation.")
        def clear(card):
            if card.get("cleanup_intent") != intent:
                raise FridayError("cleanup_pending", "The pending cleanup changed.")
            card.pop("cleanup_intent")
        state.update(root, key, clear)
        return {"cancelled": worktree}
