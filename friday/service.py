"""A macOS LaunchAgent that runs the quiet, one-shot watcher periodically."""
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import state
from .errors import FridayError

LABEL = "dev.friday.watch"


def agent_path():
    return Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")


def configuration(interval):
    if interval < 60:
        raise FridayError("invalid_interval", "Service interval must be at least 60 seconds.")
    executable = Path(__file__).resolve().parent.parent / "bin" / "friday"
    home = state.home()
    return {"Label": LABEL, "ProgramArguments": [sys.executable, str(executable), "eyes-up", "--once", "--notify-errors"],
            "StartInterval": interval, "RunAtLoad": True, "StandardOutPath": "/dev/null",
            "StandardErrorPath": str(home / "watch.log"),
            "EnvironmentVariables": {"FRIDAY_HOME": str(home), "PATH": executable_path()}}


def executable_path():
    directories = []
    for name in ("codex", "claude", "node", "uvx", "glab", "gh", "docker", "git"):
        executable = shutil.which(name)
        if executable:
            directory = str(Path(executable).parent)
            if directory not in directories:
                directories.append(directory)
    for directory in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if directory not in directories:
            directories.append(directory)
    return os.pathsep.join(directories)


def owned(path):
    try:
        with path.open("rb") as handle:
            config = plistlib.load(handle)
            original = os.fstat(handle.fileno())
    except (OSError, ValueError) as exc:
        raise FridayError("service_unavailable", str(exc))
    expected = Path(__file__).resolve().parent.parent / "bin" / "friday"
    arguments = config.get("ProgramArguments", []) if isinstance(config, dict) else []
    if not isinstance(config, dict) or config.get("Label") != LABEL or len(arguments) < 4 or arguments[1:4] != [str(expected), "eyes-up", "--once"]:
        raise FridayError("service_conflict", "LaunchAgent is not owned by this Friday installation.")
    return config, original


def refresh():
    if sys.platform != "darwin":
        raise FridayError("unsupported_platform", "The background service currently requires macOS.")
    path = agent_path()
    config, original = owned(path)
    env = config.get("EnvironmentVariables")
    if not isinstance(env, dict):
        raise FridayError("service_conflict", "LaunchAgent has no environment settings.")
    updated = executable_path()
    if env.get("PATH") == updated:
        return {"path": str(path), "updated": False}
    domain = "gui/" + str(os.getuid())
    result = subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True, text=True)
    if result.returncode:
        raise FridayError("service_unavailable", "Cannot stop the existing Friday service.")
    previous = path.read_bytes()
    temporary = None
    try:
        current = os.stat(str(path), follow_symlinks=False)
        if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
            raise FridayError("service_conflict", "LaunchAgent file changed during refresh.")
        env["PATH"] = updated
        with tempfile.NamedTemporaryFile(dir=str(path.parent), delete=False) as handle:
            temporary = handle.name
            plistlib.dump(config, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        result = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
        if result.returncode:
            path.write_bytes(previous)
            subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
            raise FridayError("service_unavailable", "Updated service could not start; previous settings restored.")
    finally:
        if temporary:
            os.unlink(temporary)
    return {"path": str(path), "updated": True}


def install(interval=300):
    if sys.platform != "darwin":
        raise FridayError("unsupported_platform", "The background service currently requires macOS.")
    config = configuration(interval)
    path = agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state.home().mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise FridayError("service_exists", "LaunchAgent already exists: " + str(path))
    with os.fdopen(fd, "wb") as handle:
        plistlib.dump(config, handle)
        handle.flush()
        os.fsync(handle.fileno())
    result = subprocess.run(["launchctl", "bootstrap", "gui/" + str(os.getuid()), str(path)], capture_output=True, text=True)
    if result.returncode:
        raise FridayError("service_unavailable", "LaunchAgent file was written, but launchctl failed: " + result.stderr.strip())
    return {"path": str(path), "interval": interval}


def uninstall():
    if sys.platform != "darwin":
        raise FridayError("unsupported_platform", "The background service currently requires macOS.")
    path = agent_path()
    config, original = owned(path)
    result = subprocess.run(["launchctl", "bootout", "gui/" + str(os.getuid()), str(path)], capture_output=True, text=True)
    if result.returncode:
        raise FridayError("service_unavailable", result.stderr.strip())
    current = os.stat(str(path), follow_symlinks=False)
    if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
        raise FridayError("service_conflict", "LaunchAgent file changed during uninstall.")
    path.unlink()
    return {"removed": str(path)}
