"""Run the project's validation command against a committed snapshot mounted read-only in Docker."""
import contextlib
import hashlib
import io
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .errors import FridayError


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
                    raise FridayError("incomplete_snapshot", "Snapshot differs from worktree: {name}", name=name)
            elif mode in (b"100644", b"100755"):
                if not item.isfile() or source.is_symlink():
                    raise FridayError("incomplete_snapshot", "Snapshot differs from worktree: {name}", name=name)
                if bool(source.stat().st_mode & 0o111) != (mode == b"100755"):
                    raise FridayError("incomplete_snapshot", "File mode differs from git: {name}", name=name)
                with source.open("rb") as handle, tar.extractfile(item) as archived:
                    while True:
                        left, right = handle.read(1024 * 1024), archived.read(1024 * 1024)
                        if left != right:
                            raise FridayError("incomplete_snapshot", "Snapshot differs from worktree: {name}", name=name)
                        if not left:
                            break
            else:
                raise FridayError("incomplete_snapshot", "Unsupported git file mode for: {name}", name=name)


def image_id(image):
    try:
        result = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                                capture_output=True, text=True)
    except FileNotFoundError:
        raise FridayError("environment_unavailable", "Docker is not available.")
    if result.returncode or not result.stdout.strip():
        raise FridayError("environment_unavailable", "Gate image is unavailable locally: {image}", image=image)
    return result.stdout.strip()


def extract(archive, path):
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts
            if member.name.startswith("/") or ".." in parts or member.isdev() or member.isfifo():
                raise FridayError("invalid_snapshot", "Unsafe path in git archive.")
        tar.extractall(path)


def links(profile):
    """gate_links: {path in the snapshot: absolute path in the image}, e.g. dependencies installed in the image
    ({"node_modules": "/app/node_modules"} or {".venv": "/opt/venv"}). gate_node_modules is the older form of the first."""
    value = dict(profile.get("gate_links") or {})
    if profile.get("gate_node_modules") is not None:
        value.setdefault("node_modules", profile["gate_node_modules"])
    for name, target in value.items():
        if (not isinstance(name, str) or not name or name.startswith("/") or ".." in Path(name).parts or
                not isinstance(target, str) or not target.startswith("/") or ".." in Path(target).parts):
            raise FridayError("invalid_profile", "gate_links must map relative snapshot paths to absolute image paths.")
    return value


def tmpfs(profile):
    """gate_tmpfs: writable scratch directories in the read-only container (caches of the tools the gate runs)."""
    value = profile.get("gate_tmpfs", [])
    if not isinstance(value, list) or any(not isinstance(path, str) or not path.startswith("/") or ".." in Path(path).parts or
                                          path.rstrip("/") in ("", "/workspace") or path.startswith("/workspace/")
                                          for path in value):
        raise FridayError("invalid_profile", "gate_tmpfs entries must be absolute container paths outside /workspace.")
    return value


def image_root(profile):
    value = profile.get("gate_image_root", "/app")
    if not isinstance(value, str) or not value.startswith("/") or ".." in Path(value).parts:
        raise FridayError("invalid_profile", "gate_image_root must be an absolute path.")
    return value.rstrip("/") or "/"


def container(profile, image, head, archive):
    """Run the gate command on the snapshot in a network-less, read-only container."""
    exec_tmp = profile.get("gate_exec_tmp", False)
    if not isinstance(exec_tmp, bool):
        raise FridayError("invalid_profile", "gate_exec_tmp must be a boolean.")
    with tempfile.TemporaryDirectory(prefix="friday-gate-") as directory:
        extract(archive, directory)
        tmp_options = ":rw,exec,nosuid,nodev" if exec_tmp else ""
        command = ["docker", "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                   "--mount", "type=bind,src=%s,dst=/workspace,readonly" % directory,
                   "--tmpfs", "/tmp" + tmp_options, "--env", "XDG_DATA_HOME=/tmp/friday-data",
                   "--env", "XDG_CACHE_HOME=/tmp/friday-cache", "--workdir", "/workspace"]
        private_tmp = profile.get("gate_private_tmp", False)
        if not isinstance(private_tmp, bool):
            raise FridayError("invalid_profile", "gate_private_tmp must be a boolean.")
        if private_tmp:
            command += ["--tmpfs", "/private/tmp" + tmp_options]
        for name, target in links(profile).items():
            if os.path.lexists(os.path.join(directory, name)):
                raise FridayError("invalid_profile", "gate_links: {path} exists in the snapshot and cannot be linked to the image.", path=name)
            os.makedirs(os.path.dirname(os.path.join(directory, name)) or directory, exist_ok=True)
            os.symlink(target, os.path.join(directory, name))
        for cache in tmpfs(profile):
            command += ["--tmpfs", cache]
        if profile.get("gate_mode", "snapshot") == "git-diff":
            common = git(profile["repository"], "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
            if "," in common:
                raise FridayError("invalid_profile", "Git directory path cannot contain a comma for Docker mounts.")
            lockfile = profile.get("gate_lockfile", "")
            if not isinstance(lockfile, str) or lockfile.startswith("/") or ".." in Path(lockfile).parts:
                raise FridayError("invalid_profile", "gate_lockfile must be a relative path without '..'.")
            script = ('env -u GIT_DIR -u GIT_WORK_TREE git init --bare -q "$GIT_DIR" && git symbolic-ref HEAD refs/heads/friday && '
                      'git update-ref refs/heads/friday "$FRIDAY_BASE" && git read-tree "$FRIDAY_HEAD" && '
                      'if [ -n "$FRIDAY_LOCKFILE" ]; then cmp "/workspace/$FRIDAY_LOCKFILE" "$FRIDAY_IMAGE_ROOT/$FRIDAY_LOCKFILE" || '
                      '{ echo "FRIDAY_ENVIRONMENT_UNAVAILABLE: image lockfile differs"; exit 125; }; fi && exec "$@"')
            command += ["--mount", "type=bind,src=%s,dst=/friday-git,readonly" % common,
                        "--env", "GIT_DIR=/tmp/friday.git", "--env", "GIT_WORK_TREE=/workspace",
                        "--env", "GIT_ALTERNATE_OBJECT_DIRECTORIES=/friday-git/objects",
                        "--env", "FRIDAY_BASE=" + profile["gate_base"], "--env", "FRIDAY_HEAD=" + head,
                        "--env", "FRIDAY_LOCKFILE=" + lockfile, "--env", "FRIDAY_IMAGE_ROOT=" + image_root(profile),
                        "--env", "GIT_CONFIG_COUNT=1", "--env", "GIT_CONFIG_KEY_0=safe.directory",
                        "--env", "GIT_CONFIG_VALUE_0=/workspace", "--entrypoint", "sh", image, "-c", script,
                        "friday"] + profile["gate"]
        else:
            command += ["--entrypoint", profile["gate"][0], image] + profile["gate"][1:]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
    return result


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SECTION = re.compile(r"^\s*—→\s+(.+)$")
SUMMARY = re.compile(r"(?i)\d+ of \d+ checks failed|\b0 (errors?|failed)\b")
FAILURE = re.compile(r"(?i)\b(error|errors|failed|fail|failing)\b|✗|×|\bTS\d{4}\b")


def check(root, profile, worktree, base, log_path, on_wait=None):
    """Run the gate on the worktree's committed HEAD for the new core; the log goes to log_path."""
    selected = dict(profile, repository=str(worktree))
    if not selected.get("gate") or not isinstance(selected.get("gate_image"), str) or not selected["gate_image"]:
        raise FridayError("invalid_profile", "An isolated gate requires gate and gate_image.")
    if selected.get("gate_mode", "snapshot") == "git-diff":
        if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40,64}", base):
            raise FridayError("invalid_plan", "A git-diff gate needs the task's base commit.")
        git(worktree, "merge-base", "--is-ancestor", base, "HEAD")
        selected["gate_base"] = base
    from . import resources
    with contextlib.ExitStack() as stack:
        # Counted slots, not global locks: machine.json decides how many gates may run at once (ADR 0005).
        for name in ["gate"] + sorted(set(selected.get("gate_resources", [])) - {"gate"}):
            stack.enter_context(resources.slot(root, name, on_wait=on_wait))
        head, digest, archive = snapshot(selected)
        image = image_id(selected["gate_image"])
        result = container(selected, image, head, archive)
        try:
            unchanged = snapshot(selected)[:2] == (head, digest)
        except FridayError:
            unchanged = False
    output = result.stdout
    environment = result.returncode and ("FRIDAY_ENVIRONMENT_UNAVAILABLE:" in output or any(
        re.search(pattern, line) for line in output.splitlines() for pattern in selected.get("environment_errors", [])))
    status = "environment_unavailable" if environment else "passed" if result.returncode == 0 and unchanged else "failed"
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    Path(log_path).write_text(output, encoding="utf-8")
    return {"status": status, "exit_code": result.returncode, "head": head, "log": str(log_path), "unchanged": unchanged}


def failures(output, limit=12000):
    """What the executor needs from a failed gate: the failed sections whole, or the error lines with context."""
    lines = [ANSI.sub("", line) for line in output.splitlines()]
    starts = [index for index, line in enumerate(lines) if SECTION.match(line)]
    chunks = []
    if starts:
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else len(lines)
            block = lines[start:end]
            if any(FAILURE.search(line) and not SUMMARY.search(line) for line in block):
                chunks.append(block)
    if not chunks:
        marked = sorted({near for index, line in enumerate(lines) if FAILURE.search(line)
                         for near in range(max(index - 2, 0), min(index + 3, len(lines)))})
        chunks = [[lines[index] for index in marked]] if marked else [lines[-80:]]
    budget = limit // max(len(chunks), 1)
    parts = []
    for block in chunks:
        text = "\n".join(block)
        parts.append(text if len(text) <= budget else text[:budget // 2] + "\n…\n" + text[-budget // 2:])
    return "\n\n".join(parts)
