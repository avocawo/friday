"""Read-only setup checks; credentials are never included in the result."""
import os
import shutil
import subprocess
import urllib.parse

from . import autopilot, browser_auth, credentials, gate, isolation, profile
from .errors import FridayError


def inspect(path):
    selected = profile.load(path)
    checks = []
    def check(name, operation):
        try:
            operation()
            checks.append({"name": name, "status": "ok"})
        except (FridayError, OSError) as exc:
            checks.append({"name": name, "status": "unavailable", "reason": str(exc)})
    check("repository", lambda: gate.git(selected["repository"], "rev-parse", "--git-dir"))
    check("automation", lambda: autopilot.preflight(selected, selected["repository"]))
    check("runner_isolation", lambda: isolation.require_runner(selected, selected["repository"]))
    check("browser_isolation", lambda: isolation.require_browser(selected))
    browser = selected.get("browser")
    if isinstance(browser, dict) and browser.get("auth") is not None:
        check("stage_auth", lambda: browser_auth.capture(browser))
    agents = selected.get("agents", {})
    if isinstance(agents, dict):
        for vendor in sorted({agent.get("vendor") for agent in agents.values() if isinstance(agent, dict) and agent.get("vendor") in ("openai", "anthropic", "zai")}):
            def probe(provider=vendor):
                binary = "codex" if provider == "openai" else "claude" if provider in ("anthropic", "zai") else None
                if not binary or not shutil.which(binary):
                    raise FridayError("runner_unavailable", "Provider CLI is not available.")
                if provider == "zai":
                    configured = next(agent for agent in agents.values() if isinstance(agent, dict) and agent.get("vendor") == provider)
                    credentials.token({"token_env": "FRIDAY_ZAI_TOKEN", "token_file": configured.get("token_file", "~/.config/friday/zai-token"),
                                       "token_key": configured.get("token_key")}, "FRIDAY_ZAI_TOKEN")
                elif provider == "anthropic" and os.environ.get("FRIDAY_CLAUDE_API_KEY"):
                    return
                else:
                    command = [binary, "login", "status"] if provider == "openai" else [binary, "auth", "status"]
                    try:
                        status = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                    except (OSError, subprocess.TimeoutExpired):
                        raise FridayError("runner_unavailable", "Cannot inspect provider authentication.")
                    if status.returncode:
                        raise FridayError("credentials_missing", "Provider authentication is unavailable.")
            check("agent_" + vendor, probe)
    for service, default in (("tracker", "FRIDAY_TRACKER_TOKEN"), ("gitlab", "FRIDAY_GITLAB_TOKEN"),
                             ("sentry", "FRIDAY_SENTRY_TOKEN")):
        config = selected.get(service)
        if isinstance(config, dict):
            if service == "gitlab" and config.get("backend") == "glab":
                def gitlab_auth():
                    host = urllib.parse.urlsplit(config.get("base_url", "")).netloc
                    if not host or not shutil.which("glab"):
                        raise FridayError("credentials_missing", "GitLab CLI or host is unavailable.")
                    try:
                        result = subprocess.run(["glab", "auth", "status", "--hostname", host], stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL, timeout=10)
                    except (OSError, subprocess.TimeoutExpired):
                        raise FridayError("credentials_missing", "Cannot inspect GitLab CLI authentication.")
                    if result.returncode:
                        raise FridayError("credentials_missing", "GitLab CLI authentication is unavailable.")
                check("gitlab_credentials", gitlab_auth)
                if config.get("token_file") or config.get("token_env"):
                    check("gitlab_action_credentials", lambda cfg=config: credentials.token(cfg, "FRIDAY_GITLAB_TOKEN"))
            else:
                check(service + "_credentials", lambda cfg=config, env=default: credentials.token(cfg, env))
    tracker = selected.get("tracker")
    if isinstance(tracker, dict):
        check("tracker_organization", lambda: credentials.organization(tracker))
    return {"profile": selected["name"], "ready": all(item["status"] == "ok" for item in checks), "checks": checks}
