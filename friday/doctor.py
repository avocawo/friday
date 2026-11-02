"""Is this machine and profile ready to run tasks? Every check says what is missing, never a secret value."""
import shutil
import subprocess
import sys

from . import codex_policy, models, profile as profiles, resources, state
from .errors import FridayError


def inspect(path):
    checks = []
    def check(name, operation, warning=False):
        try:
            detail = operation()
            checks.append({"name": name, "status": "ok", **({"detail": detail} if isinstance(detail, str) and detail else {})})
        except (FridayError, OSError, subprocess.SubprocessError) as exc:
            checks.append({"name": name, "status": "warning" if warning else "missing", "reason": str(exc)})
    selected = {}
    check("profile", lambda: selected.update(profiles.load(path)))
    if not selected:
        return {"ready": False, "checks": checks}
    def repository():
        result = subprocess.run(["git", "-C", selected["repository"], "rev-parse", "--git-dir"], capture_output=True)
        if result.returncode:
            raise FridayError("not_a_repository", "Not a git repository: " + selected["repository"])
        if not selected.get("worktree_root") or not selected.get("base_branch"):
            raise FridayError("invalid_profile", "worktree_root and base_branch are required.")
    check("repository", repository)
    layout = {}
    def cast():
        value, notes = models.cast(selected, models.availability(state.home()))
        layout.update(value)
        return "; ".join(notes)
    check("models", cast)
    catalog = models.catalog(selected) if layout else {}
    drivers = {entry["driver"] for entry in catalog.values()}
    if "claude" in drivers:
        check("claude_cli", lambda: tool_version("claude"))
        check("executor_sandbox", sandbox)
    if "codex" in drivers:
        def codex():
            executable = shutil.which("codex")
            if not executable:
                raise FridayError("missing", "Codex CLI is not installed.")
            codex_policy.verify_sandbox(executable)
        check("codex_cli_and_sandbox", codex)
    for name, entry in catalog.items():
        if entry["vendor"] in models.VENDOR_ENDPOINTS:
            check("token:" + name, lambda entry=entry: models.environment(entry, selected) and None)
    if not selected.get("executor_commands"):
        checks.append({"name": "executor_commands", "status": "warning",
                       "reason": "No executor_commands: the executor cannot run lint, typecheck or tests itself."})
    if selected.get("gate"):
        def gate_image():
            from . import gate
            gate.image_id(selected.get("gate_image"))
        check("gate_image", gate_image)
    else:
        checks.append({"name": "gate", "status": "warning", "reason": "No gate: milestones are reviewed without an isolated check."})
    if isinstance(selected.get("browser"), dict):
        def browser():
            from . import browser_probe, browser_session
            browser_session.configuration(selected["browser"], "http://probe.localhost:12345/")
            browser_probe.verify(selected)
        check("browser", browser)
        def port():
            value = resources.port(state.home(), "doctor-probe")
            resources.release_port(state.home(), "doctor-probe")
            return "free ports from %d" % value
        check("preview_port", port)
    delivery = (selected.get("delivery") or {}).get("transport", "none")
    if delivery == "github":
        check("delivery_github", lambda: tool_version("gh"))
    elif delivery == "gitlab":
        check("delivery_gitlab", lambda: tool_version("glab"))
    else:
        checks.append({"name": "delivery", "status": "warning", "reason": "No delivery transport: the MR text is only saved."})
    if selected.get("tracker") is None:
        checks.append({"name": "tracker", "status": "warning", "reason": "No tracker: start needs --brief."})
    return {"ready": all(item["status"] != "missing" for item in checks), "checks": checks, "cast": layout}


def tool_version(name):
    executable = shutil.which(name)
    if not executable:
        raise FridayError("missing", name + " is not installed.")
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise FridayError("missing", name + " does not run.")
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""


def sandbox():
    """Claude Code's shell sandbox needs Seatbelt on macOS or bubblewrap on Linux; without it executors refuse to start."""
    needed = "sandbox-exec" if sys.platform == "darwin" else "bwrap"
    if not shutil.which(needed):
        raise FridayError("missing", needed + " is not available; the executor sandbox cannot start.")


def render(report):
    marks = {"ok": "✓", "warning": "!", "missing": "✗"}
    lines = []
    for item in report["checks"]:
        text = item.get("reason") or item.get("detail")
        lines.append("%s %s%s" % (marks[item["status"]], item["name"], ": " + text if text else ""))
    if report.get("cast"):
        lines.append("Раскладка: " + ", ".join("%s → %s%s" % (role, entry["model"], " " + entry["effort"] if entry.get("effort") else "")
                                             for role, entry in report["cast"].items()))
    lines.append("Готово к работе." if report["ready"] else "Не готово: исправьте пункты со ✗.")
    return "\n".join(lines)
