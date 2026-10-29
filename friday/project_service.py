"""A separate macOS LaunchAgent for one project's autonomous cycle."""
import hashlib
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from . import doctor, mission, profile, service, state
from .errors import FridayError


def configuration(profile_path, interval):
    if interval < 60:
        raise FridayError("invalid_interval", "Project service interval must be at least 60 seconds.")
    selected = profile.load(profile_path)
    identity = hashlib.sha256(selected["path"].encode()).hexdigest()[:16]
    label = "dev.friday.project." + identity
    executable = Path(__file__).resolve().parent.parent / "bin" / "friday"
    home = state.home()
    return {"Label": label, "ProgramArguments": [sys.executable, str(executable), "long-weekend", "next", "--profile", selected["path"]],
            "StartInterval": interval, "RunAtLoad": True, "StandardOutPath": "/dev/null",
            "StandardErrorPath": str(home / ("project-" + identity + ".log")),
            "EnvironmentVariables": {"FRIDAY_HOME": str(home), "PATH": service.executable_path()}}


def agent_path(config):
    return Path.home() / "Library" / "LaunchAgents" / (config["Label"] + ".plist")


def install(profile_path, interval=300):
    if sys.platform != "darwin":
        raise FridayError("unsupported_platform", "The project service requires macOS.")
    selected = profile.load(profile_path)
    settings = mission.config_for(selected)
    if not settings or settings.get("transport") != "github":
        raise FridayError("delivery_disabled", "Project service requires configured GitHub delivery.")
    diagnostics = doctor.inspect(profile_path)
    if not diagnostics["ready"]:
        raise FridayError("environment_unavailable", "Project diagnostics did not pass; run friday doctor for details.")
    config = configuration(profile_path, interval)
    path = agent_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.home().mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise FridayError("service_exists", "Project service already exists: " + str(path))
    with os.fdopen(descriptor, "wb") as handle:
        plistlib.dump(config, handle)
        handle.flush()
        os.fsync(handle.fileno())
    result = subprocess.run(["launchctl", "bootstrap", "gui/" + str(os.getuid()), str(path)], capture_output=True, text=True)
    if result.returncode:
        raise FridayError("service_unavailable", "Project service was written but could not start.")
    return {"path": str(path), "interval": interval}


def uninstall(profile_path):
    if sys.platform != "darwin":
        raise FridayError("unsupported_platform", "The project service requires macOS.")
    expected = configuration(profile_path, 300)
    path = agent_path(expected)
    try:
        with path.open("rb") as handle:
            current = plistlib.load(handle)
            original = os.fstat(handle.fileno())
    except (OSError, ValueError) as exc:
        raise FridayError("service_unavailable", str(exc))
    if not isinstance(current, dict) or current.get("Label") != expected["Label"] or current.get("ProgramArguments") != expected["ProgramArguments"]:
        raise FridayError("service_conflict", "Project service is not owned by this Friday installation.")
    result = subprocess.run(["launchctl", "bootout", "gui/" + str(os.getuid()), str(path)], capture_output=True, text=True)
    if result.returncode:
        raise FridayError("service_unavailable", "Cannot stop the project service.")
    observed = os.stat(str(path), follow_symlinks=False)
    if (original.st_dev, original.st_ino) != (observed.st_dev, observed.st_ino):
        raise FridayError("service_conflict", "Project service file changed during removal.")
    path.unlink()
    return {"removed": str(path)}
