"""friday service: run friday serve as a user service — a launchd agent on macOS, a systemd user unit on Linux (ADR 0014).

A service starts with a bare environment, so its file names every path itself: the tools Friday calls, FRIDAY_HOME and HOME.
"""
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .errors import FridayError
from .serve import FRIDAY

LABEL = "local.friday.serve"
TOOLS = ("claude", "codex", "docker", "git")
# Found now → their directories go on the service's PATH too: delivery, the tracker server, browser tooling.
OPTIONAL = ("glab", "gh", "uvx", "node", "npx")
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def environment(root, which=shutil.which):
    """The service's whole environment: a PATH from the directories of the tools found now, FRIDAY_HOME and HOME."""
    missing = [tool for tool in TOOLS if not which(tool)]
    if missing:
        raise FridayError("environment_unavailable", "Not found in PATH, the service could not run them: " + ", ".join(missing))
    directories = []
    for tool in TOOLS + tuple(tool for tool in OPTIONAL if which(tool)):
        directory = str(Path(which(tool)).parent)  # not resolved: a node script needs node from its own bin directory
        if directory not in directories:
            directories.append(directory)
    python = str(Path(sys.executable).parent)
    if python not in directories:
        directories.append(python)
    directories += [item for item in SYSTEM_PATH if item not in directories]
    return {"PATH": ":".join(directories), "FRIDAY_HOME": str(Path(root).resolve()), "HOME": str(Path.home())}


def command():
    return [sys.executable, str(FRIDAY), "serve"]


def launchd(root, env):
    log = str(Path(root).resolve() / "serve.log")
    return plistlib.dumps({"Label": LABEL, "ProgramArguments": command(), "EnvironmentVariables": env,
                           "RunAtLoad": True, "KeepAlive": True, "StandardOutPath": log, "StandardErrorPath": log}).decode()


def systemd(root, env):
    lines = ["[Unit]", "Description=Friday task queue (friday serve)", "", "[Service]",
             "ExecStart=" + " ".join('"%s"' % part for part in command())]
    lines += ['Environment="%s=%s"' % item for item in sorted(env.items())]
    # KillMode=process: restarting the server must not kill the tasks it started in their own sessions.
    lines += ["Restart=always", "KillMode=process", "", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def target(system=None):
    system = system or platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
    if system == "Linux":
        return Path.home() / ".config" / "systemd" / "user" / "friday-serve.service"
    raise FridayError("unsupported", "friday service supports macOS (launchd) and Linux (systemd).")


def render(root, system=None, which=shutil.which):
    system = system or platform.system()
    env = environment(root, which)
    return target(system), (launchd(root, env) if system == "Darwin" else systemd(root, env))


def install(root):
    path, text = render(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if platform.system() == "Darwin":
        domain = "gui/%d" % os.getuid()
        subprocess.run(["launchctl", "bootout", domain + "/" + LABEL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    else:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "friday-serve.service"], check=True)
    return path


def uninstall():
    path = target()
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), LABEL)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.run(["systemctl", "--user", "disable", "--now", "friday-serve.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    path.unlink(missing_ok=True)
    return path


def check(root, profile_path, run=subprocess.run, which=shutil.which):
    """friday doctor under exactly the environment the service file declares: what the service will and will not find."""
    env = environment(root, which)
    result = run([sys.executable, str(FRIDAY), "doctor", "--profile", str(profile_path)], env=env,
                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return result.returncode, result.stdout
