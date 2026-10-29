"""Start and resume a project brief through the existing task workflow."""
from pathlib import Path

from . import autopilot, github, intake, journal, mission, profile, state
from .errors import FridayError
from .locks import locked


def start(root, key, profile_path, brief_path):
    selected = profile.load(profile_path)
    if not selected.get("worktree_root") or not selected.get("base_branch"):
        raise FridayError("invalid_profile", "Project mode requires worktree_root and base_branch.")
    try:
        brief = Path(brief_path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise FridayError("invalid_brief", "Cannot read project brief: " + str(exc))
    if not brief.strip() or len(brief.encode("utf-8")) > 100000:
        raise FridayError("invalid_brief", "Project brief must be non-empty and at most 100000 bytes.")
    summary = next(line.strip().lstrip("#").strip() for line in brief.splitlines() if line.strip())
    if not summary:
        raise FridayError("invalid_brief", "The first non-empty line must contain a project title.")
    ticket = {"key": key, "summary": summary[:200], "description": brief, "kind": "project"}
    state.create(root, key, selected["path"], ticket, "auto")
    return run(root, key)


def run(root, key):
    with locked(root, "project:" + state.validate_key(key), timeout=1800):
        card = state.read(root, key)
        ticket = card.get("ticket")
        if not isinstance(ticket, dict) or ticket.get("kind") not in ("project", "github_issue"):
            raise FridayError("not_project", "This card was not started in project mode.")
        selected = profile.load(card["profile"])
        if not card.get("worktree"):
            intake.prepare(root, key, selected)
        journal.sync(root, key)
        if mission.config_for(selected):
            state.update(root, key, lambda current: current.update(autonomous_requested=True))
            return mission.advance(root, key)
        return autopilot.run(root, key)


def next_issue(root, profile_path):
    selected = profile.load(profile_path)
    if not selected.get("worktree_root") or not selected.get("base_branch"):
        raise FridayError("invalid_profile", "Project mode requires worktree_root and base_branch.")
    settings = mission.config_for(selected)
    if not settings or settings.get("transport") != "github":
        raise FridayError("delivery_disabled", "GitHub backlog automation requires configured GitHub delivery.")
    github.check_delivery(selected, settings)
    with locked(root, "github-backlog:" + github.repository(selected), timeout=1800):
        for card in state.all_tasks(root):
            if card.get("profile") != selected["path"] or (card.get("ticket") or {}).get("kind") != "github_issue":
                continue
            if card.get("merge_request"):
                pull = github.get_pull(selected, card["merge_request"])
                if pull.get("state") != "opened":
                    continue
                return {"selected": card["key"], "active": True, "result": mission.advance(root, card["key"])}
            return {"selected": card["key"], "active": True, "result": run(root, card["key"])}
        issue = github.select_issue(selected, root)
        if issue is None:
            return {"selected": None}
        github.assert_issue_ready(selected, issue["number"])
        state.create(root, issue["key"], selected["path"], issue, "auto")
        result = run(root, issue["key"])
        return {"selected": issue["key"], "result": result}
