"""Worktree identity and content snapshots for local automated commits."""
import hashlib
import os
import stat

from . import gate
from .errors import FridayError


def verify(repository, worktree, branch):
    records = gate.git(repository, "worktree", "list", "--porcelain", "-z").decode("utf-8", "surrogateescape")
    records = [record.split("\0") for record in records.split("\0\0")]
    if not any("worktree " + worktree in record and "branch refs/heads/" + branch in record for record in records):
        raise FridayError("worktree_conflict", "Path and branch are not registered in the project repository.")
    common = gate.git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    other = gate.git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    if os.path.realpath(common) != os.path.realpath(other):
        raise FridayError("worktree_conflict", "Worktree belongs to a different repository.")


def fingerprint(worktree):
    digest = hashlib.sha256()
    def add(data):
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    add(gate.git(worktree, "rev-parse", "HEAD"))
    add(gate.git(worktree, "ls-files", "--stage", "-z"))
    add(gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"))
    files = gate.git(worktree, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    for raw in sorted(set(filter(None, files.split(b"\0")))):
        add(raw)
        path = os.path.join(worktree, os.fsdecode(raw))
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            add(b"deleted")
            continue
        add(str(info.st_mode).encode())
        if stat.S_ISLNK(info.st_mode):
            add(os.fsencode(os.readlink(path)))
        elif stat.S_ISREG(info.st_mode):
            content = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    content.update(chunk)
            add(content.digest())
        else:
            raise FridayError("unsafe_worktree", "Unsupported worktree entry: " + os.fsdecode(raw))
    return digest.hexdigest()
