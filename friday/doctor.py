"""Is this machine and profile ready to run tasks? Every check says what is missing, never a secret value."""
import shutil
import subprocess
import sys

from . import codex_policy, i18n, models, profile as profiles, resources, state, tracker
from .errors import FridayError
from .i18n import N_, _


def inspect(path):
    checks = []
    def check(name, operation, warning=False):
        try:
            detail = operation()
            checks.append({"name": name, "status": "ok", **({"detail": detail} if isinstance(detail, str) and detail else {})})
        except (FridayError, OSError, subprocess.SubprocessError) as exc:
            item = {"name": name, "status": "warning" if warning else "missing", "reason": str(exc)}
            if i18n.error(exc) != str(exc):
                item["localized"] = i18n.error(exc)
            checks.append(item)
    selected = {}
    check("profile", lambda: selected.update(profiles.load(path)))
    if not selected:
        return {"ready": False, "checks": checks}
    def repository():
        result = subprocess.run(["git", "-C", selected["repository"], "rev-parse", "--git-dir"], capture_output=True)
        if result.returncode:
            raise FridayError("not_a_repository", "Not a git repository: {path}", path=selected["repository"])
        if not selected.get("worktree_root") or not selected.get("base_branch"):
            raise FridayError("invalid_profile", "worktree_root and base_branch are required.")
    check("repository", repository)
    layout, shown = {}, []
    def cast():
        value, notes = models.cast(selected, models.availability(state.home()))
        layout.update(value)
        shown.extend(i18n.show(note) for note in notes)
        return "; ".join(notes)
    check("models", cast)
    if shown and "; ".join(shown) != checks[-1].get("detail"):
        checks[-1]["localized"] = "; ".join(shown)
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
        if models.endpoint(entry):
            check("token:" + name, lambda entry=entry: models.environment(entry, selected) and None)
    if not selected.get("executor_commands"):
        checks.append({"name": "executor_commands", "status": "warning",
                       "reason": N_("No executor_commands: the executor cannot run lint, typecheck or tests itself.")})
    if selected.get("repo_map") == "graphify":
        check("graphify", lambda: tool_version("graphify"))
        if selected.get("repo_map_mcp"):
            def graphify_mcp():
                if not shutil.which("graphify-mcp"):
                    raise FridayError("missing", "graphify-mcp is not installed (uv tool install 'graphifyy[mcp]').")
            check("graphify_mcp", graphify_mcp)
    if selected.get("gate"):
        def gate_image():
            from . import gate
            gate.image_id(selected.get("gate_image"))
        check("gate_image", gate_image)
    else:
        checks.append({"name": "gate", "status": "warning", "reason": N_("No gate: milestones are reviewed without an isolated check.")})
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
        checks.append({"name": "delivery", "status": "warning", "reason": N_("No delivery transport: the MR text is only saved.")})
    if isinstance(selected.get("tracker"), dict) and tracker.tool(selected["tracker"]):
        check("tracker_" + tracker.tool(selected["tracker"]), lambda: tool_version(tracker.tool(selected["tracker"])))
    if selected.get("tracker") is None:
        checks.append({"name": "tracker", "status": "warning", "reason": N_("No tracker: start needs --brief.")})
    return {"ready": all(item["status"] != "missing" for item in checks), "checks": checks, "cast": layout}


def tool_version(name):
    executable = shutil.which(name)
    if not executable:
        raise FridayError("missing", "{tool} is not installed.", tool=name)
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise FridayError("missing", "{tool} does not run.", tool=name)
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""


def sandbox():
    """Claude Code's shell sandbox needs Seatbelt on macOS or bubblewrap on Linux; without it executors refuse to start."""
    needed = "sandbox-exec" if sys.platform == "darwin" else "bwrap"
    if not shutil.which(needed):
        raise FridayError("missing", "{tool} is not available; the executor sandbox cannot start.", tool=needed)


def render(report):
    marks = {"ok": "✓", "warning": "!", "missing": "✗"}
    lines = []
    for item in report["checks"]:
        text = item.get("localized") or (_(item["reason"]) if item.get("reason") else item.get("detail"))
        lines.append("%s %s%s" % (marks[item["status"]], item["name"], ": " + text if text else ""))
    if report.get("cast"):
        lines.append(_("Models: {cast}", cast=", ".join("%s → %s%s" % (role, entry["model"], " " + entry["effort"] if entry.get("effort") else "")
                                                       for role, entry in report["cast"].items())))
    lines.append(_("Ready to work.") if report["ready"] else _("Not ready: fix the items marked ✗."))
    return "\n".join(lines)
