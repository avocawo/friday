"""Require bounded provider adapters; browser isolation still fails closed."""
import shutil
import subprocess
import sys
from pathlib import Path

from . import browser_probe, browser_session, codex_policy
from .errors import FridayError


def require_runner(selected=None, workspace=None):
    if sys.platform != "darwin" or not isinstance(selected, dict) or not isinstance(workspace, str):
        raise FridayError("isolation_unavailable", "Runner isolation requires a verified macOS workspace.")
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise FridayError("isolation_unavailable", "The runner workspace does not exist.")
    definitions = []
    for field in ("runners", "agents"):
        entries = selected.get(field, {})
        if not isinstance(entries, dict) or any(not isinstance(value, dict) for value in entries.values()):
            raise FridayError("invalid_profile", field + " must be an object of definitions.")
        definitions.extend(entries.values())
    if not definitions:
        raise FridayError("isolation_unavailable", "No bounded model adapters are configured.")
    codex_needed = False
    claude_needed = False
    for item in definitions:
        command = item.get("command")
        vendor = item.get("vendor")
        if command == ["python3", "{codex_adapter}"] and vendor in (None, "openai"):
            codex_needed = True
        elif command == ["python3", "{claude_adapter}"] and vendor in ("anthropic", "zai"):
            claude_needed = True
        else:
            raise FridayError("isolation_unavailable", "Only Friday's bounded Codex and Claude adapters are allowed.")
    if codex_needed:
        executable = shutil.which("codex")
        if not executable:
            raise FridayError("isolation_unavailable", "Codex CLI is unavailable.")
        codex_policy.verify_environment(executable, workspace)
        codex_policy.verify_sandbox(executable)
    if claude_needed:
        executable = shutil.which("claude")
        if not executable:
            raise FridayError("isolation_unavailable", "Claude Code is unavailable.")
        try:
            result = subprocess.run([executable, "--help"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            raise FridayError("isolation_unavailable", "Cannot verify Claude Code restrictions.")
        if result.returncode or any(option not in result.stdout for option in ("--safe-mode", "--restricted", "--strict-mcp-config", "--no-chrome", "--tools")):
            raise FridayError("isolation_unavailable", "Claude Code does not expose required restrictions.")


def require_browser(selected=None):
    if not isinstance(selected, dict):
        raise FridayError("isolation_unavailable", "A verified browser profile is required.")
    if isinstance(selected.get("browser"), dict) and selected["browser"].get("mode") == "local":
        if not isinstance(selected.get("preview"), dict) or selected["preview"].get("backend") != "docker-local":
            raise FridayError("isolation_unavailable", "Local browser mode requires an isolated docker-local preview.")
    browser_session.configuration(selected.get("browser"), "http://probe.localhost:12345/")
    browser_probe.verify(selected)


def require_release_build():
    raise FridayError("isolation_unavailable", "Release builds are paused until their network and side effects are isolated from production services.")
