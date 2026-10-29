"""Run a gate against a committed snapshot mounted read-only in Docker."""
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path

from . import state
from .errors import FridayError
from .locks import locked


def git(repository, *args):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    filters = subprocess.run(["git", "-C", str(repository), "config", "--name-only", "--get-regexp",
                              r"^filter\..*\.(clean|process|smudge)$"], capture_output=True, env=environment)
    if filters.returncode not in (0, 1):
        raise FridayError("git_error", "Cannot check Git filter configuration.")
    if filters.stdout:
        raise FridayError("isolation_unavailable", "Git external filters must be disabled before running Friday.")
    if args and args[0] == "diff":
        args = ("diff", "--no-ext-diff", "--no-textconv", *args[1:])
    result = subprocess.run(["git", "-C", str(repository), "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args], capture_output=True, env=environment)
    if result.returncode:
        raise FridayError("git_error", result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def snapshot(profile):
    repository = profile["repository"]
    if git(repository, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise FridayError("dirty_tree", "Commit or remove all changes before running an isolated gate.")
    index = git(repository, "ls-files", "--stage", "-z")
    if any(item.startswith(b"160000 ") for item in index.split(b"\0")):
        raise FridayError("unsupported_submodule", "Isolated gate snapshots do not support submodules.")
    head = git(repository, "rev-parse", "HEAD").decode().strip()
    archive = git(repository, "archive", "--format=tar", head)
    verify_archive(repository, archive, index)
    digest = hashlib.sha256(archive).hexdigest()
    return head, digest, archive


def verify_archive(repository, archive, index):
    # Git archive attributes can omit or rewrite tracked files. Do not claim a
    # check of the worktree if the mounted snapshot differs from that worktree.
    expected = {}
    for entry in index.split(b"\0"):
        if entry:
            metadata, filename = entry.split(b"\t", 1)
            expected[os.fsdecode(filename)] = metadata.split(b" ", 1)[0]
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        members = {item.name: item for item in tar.getmembers() if not item.isdir()}
        if set(members) != set(expected):
            raise FridayError("incomplete_snapshot", "Git archive does not contain exactly the tracked files.")
        for name, mode in expected.items():
            source = Path(repository) / name
            item = members[name]
            if mode == b"120000":
                if not source.is_symlink() or not item.issym() or os.readlink(source) != item.linkname:
                    raise FridayError("incomplete_snapshot", "Snapshot differs from worktree: " + name)
            elif mode in (b"100644", b"100755"):
                if not item.isfile() or source.is_symlink():
                    raise FridayError("incomplete_snapshot", "Snapshot differs from worktree: " + name)
                if bool(source.stat().st_mode & 0o111) != (mode == b"100755"):
                    raise FridayError("incomplete_snapshot", "File mode differs from git: " + name)
                with source.open("rb") as handle, tar.extractfile(item) as archived:
                    while True:
                        left, right = handle.read(1024 * 1024), archived.read(1024 * 1024)
                        if left != right:
                            raise FridayError("incomplete_snapshot", "Snapshot differs from worktree: " + name)
                        if not left:
                            break
            else:
                raise FridayError("incomplete_snapshot", "Unsupported git file mode for: " + name)


def definition(profile, image_id):
    payload = {"gate": profile["gate"], "image": image_id, "mode": profile.get("gate_mode", "snapshot"),
               "base": profile.get("gate_base"), "node_modules": profile.get("gate_node_modules"),
               "lockfile": profile.get("gate_lockfile"), "tmpfs": profile.get("gate_tmpfs", []),
               "private_tmp": profile.get("gate_private_tmp", False),
               "runner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def with_baseline(profile, card):
    selected = dict(profile)
    mode = selected.get("gate_mode", "snapshot")
    if mode not in ("snapshot", "git-diff"):
        raise FridayError("invalid_profile", "Unknown gate mode: " + str(mode))
    if mode == "git-diff":
        base = card.get("plan_base")
        if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40,64}", base):
            raise FridayError("invalid_plan", "A git-diff gate needs the plan's base commit.")
        git(selected["repository"], "merge-base", "--is-ancestor", base, "HEAD")
        selected["gate_base"] = base
    return selected


def image_id(image):
    try:
        result = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                                capture_output=True, text=True)
    except FileNotFoundError:
        raise FridayError("environment_unavailable", "Docker is not available.")
    if result.returncode or not result.stdout.strip():
        raise FridayError("environment_unavailable", "Gate image is unavailable locally: " + image)
    return result.stdout.strip()


def extract(archive, path):
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts
            if member.name.startswith("/") or ".." in parts or member.isdev() or member.isfifo():
                raise FridayError("invalid_snapshot", "Unsafe path in git archive.")
        tar.extractall(path)


def execute(root, key, stage, profile):
    if not profile.get("gate") or not isinstance(profile.get("gate_image"), str) or not profile["gate_image"]:
        raise FridayError("invalid_profile", "An isolated gate requires gate and gate_image.")
    resources = profile.get("gate_resources", [])
    if not isinstance(resources, list) or any(not isinstance(x, str) or not x for x in resources):
        raise FridayError("invalid_profile", "gate_resources must be an array of names.")
    names = sorted(set(resources + ["branch:" + profile["repository"]]))
    with contextlib.ExitStack() as stack:
        for name in names:
            stack.enter_context(locked(root, "resource:" + name))
        card = state.read(root, key)
        if stage not in card["stages"] or card["stages"][stage].get("status") != "open":
            raise FridayError("invalid_stage", "Gate requires an open stage: " + stage)
        profile = with_baseline(profile, card)
        head, digest, archive = snapshot(profile)
        try:
            image = image_id(profile["gate_image"])
        except FridayError as exc:
            if exc.code == "environment_unavailable":
                def unavailable(current):
                    current["stages"][stage]["gate"] = {"status": "environment_unavailable", "message": str(exc),
                                                          "at": state.now()}
                    current["stages"][stage]["environment_lock"] = True
                state.update(root, key, unavailable)
            raise
        fingerprint = definition(profile, image)
        with tempfile.TemporaryDirectory(prefix="friday-gate-") as directory:
            extract(archive, directory)
            command = ["docker", "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                       "--mount", "type=bind,src=%s,dst=/workspace,readonly" % directory,
                       "--tmpfs", "/tmp", "--env", "XDG_DATA_HOME=/tmp/friday-data",
                       "--env", "XDG_CACHE_HOME=/tmp/friday-cache", "--workdir", "/workspace"]
            private_tmp = profile.get("gate_private_tmp", False)
            if not isinstance(private_tmp, bool):
                raise FridayError("invalid_profile", "gate_private_tmp must be a boolean.")
            if private_tmp:
                command += ["--tmpfs", "/private/tmp"]
            if profile.get("gate_mode", "snapshot") == "git-diff":
                common = git(profile["repository"], "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
                if "," in common:
                    raise FridayError("invalid_profile", "Git directory path cannot contain a comma for Docker mounts.")
                node_modules = profile.get("gate_node_modules")
                if node_modules is not None:
                    if not isinstance(node_modules, str) or not node_modules.startswith("/") or os.path.lexists(os.path.join(directory, "node_modules")):
                        raise FridayError("invalid_profile", "gate_node_modules must name an absolute image path; snapshot must not contain node_modules.")
                    os.symlink(node_modules, os.path.join(directory, "node_modules"))
                caches = profile.get("gate_tmpfs", [])
                if not isinstance(caches, list) or any(not isinstance(cache, str) or not node_modules or
                                                        not cache.startswith(node_modules.rstrip("/") + "/.") or
                                                        ".." in Path(cache).parts for cache in caches):
                    raise FridayError("invalid_profile", "gate_tmpfs entries must be hidden cache directories under gate_node_modules.")
                for cache in caches:
                    command += ["--tmpfs", cache]
                lockfile = profile.get("gate_lockfile", "")
                if not isinstance(lockfile, str) or lockfile.startswith("/") or ".." in Path(lockfile).parts:
                    raise FridayError("invalid_profile", "gate_lockfile must be a relative path without '..'.")
                script = ('env -u GIT_DIR -u GIT_WORK_TREE git init --bare -q "$GIT_DIR" && git symbolic-ref HEAD refs/heads/friday && '
                          'git update-ref refs/heads/friday "$FRIDAY_BASE" && git read-tree "$FRIDAY_HEAD" && '
                          'if [ -n "$FRIDAY_LOCKFILE" ]; then cmp "/workspace/$FRIDAY_LOCKFILE" "/app/$FRIDAY_LOCKFILE" || '
                          '{ echo "FRIDAY_ENVIRONMENT_UNAVAILABLE: image lockfile differs"; exit 125; }; fi && exec "$@"')
                command += ["--mount", "type=bind,src=%s,dst=/friday-git,readonly" % common,
                            "--env", "GIT_DIR=/tmp/friday.git", "--env", "GIT_WORK_TREE=/workspace",
                            "--env", "GIT_ALTERNATE_OBJECT_DIRECTORIES=/friday-git/objects",
                            "--env", "FRIDAY_BASE=" + profile["gate_base"], "--env", "FRIDAY_HEAD=" + head,
                            "--env", "FRIDAY_LOCKFILE=" + lockfile,
                            "--env", "GIT_CONFIG_COUNT=1", "--env", "GIT_CONFIG_KEY_0=safe.directory",
                            "--env", "GIT_CONFIG_VALUE_0=/workspace", "--entrypoint", "sh", image, "-c", script,
                            "friday"] + profile["gate"]
            else:
                command += ["--entrypoint", profile["gate"][0], image] + profile["gate"][1:]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, errors="replace")
        try:
            after_head, after_digest, _ = snapshot(profile)
            unchanged = (head, digest) == (after_head, after_digest)
        except FridayError:
            unchanged = False
        output = result.stdout
        matches = [line for line in output.splitlines()
                   if any(re.search(pattern, line) for pattern in profile.get("environment_errors", []))]
        outcome = "passed" if result.returncode == 0 and unchanged else "failed"
        if result.returncode and (matches or "FRIDAY_ENVIRONMENT_UNAVAILABLE:" in output):
            outcome = "environment_unavailable"
        evidence_dir = state.task_path(root, key).parent / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        log = evidence_dir / (uuid.uuid4().hex + ".log")
        log.write_text(output, encoding="utf-8")
        record = {"status": outcome, "exit_code": result.returncode, "head": head,
                  "snapshot": digest, "definition": fingerprint, "image": image,
                  "log": str(log), "at": state.now(), "unchanged": unchanged,
                  "decision_revision": card.get("decision_revision", 0), "plan_revision": card.get("plan_revision", 0)}
        def save(current):
            selected = current["stages"].get(stage)
            if not selected or selected.get("status") != "open":
                raise FridayError("invalid_stage", "Stage changed while gate was running.")
            if current.get("decision_revision", 0) != card.get("decision_revision", 0):
                raise FridayError("stale_evidence", "A decision changed while the gate was running.")
            if current.get("plan_revision", 0) != card.get("plan_revision", 0):
                raise FridayError("stale_evidence", "The plan changed while the gate was running.")
            selected["gate"] = record
            if outcome == "environment_unavailable":
                selected["environment_lock"] = True
            elif outcome == "passed" and current.get("mode") == "auto":
                selected.pop("environment_lock", None)
            current["events"].append({"type": "gate", "stage": stage, "status": outcome, "at": state.now()})
        state.update(root, key, save)
        return record
