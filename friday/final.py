"""The end of a task: browser acceptance of UI milestones, final review with a blind check, and the draft merge request."""
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import card as cards
from . import gate, models, resources
from .errors import FridayError

ADAPTERS = Path(__file__).resolve().parent.parent / "adapters"

BROWSER_RULES = (
    "Check the behavior criteria of one milestone in a local preview of the application, using only the friday browser "
    "tools. Call take_snapshot first. For each criterion open the route or menu the milestone changes, wait until loading "
    "and redirects settle, then act with real clicks, typing and keys and observe the page and the relevant network "
    "requests. To check a phone or desktop layout, call emulate with a viewport on the current page and restore it before "
    "the next full navigation. A login screen or 401 seen while loading is checked again after loading before it counts. "
    "Never bypass authorization and never open get_network_request on authenticated requests. Judge each criterion pass, "
    "fail or unverifiable with what you actually observed; when the preview or a tool is unavailable say unverifiable, do "
    "not infer a defect. Report a blocking finding only for behaviour you saw fail. Write in the task's language.")
FINAL_RULES = (
    "You do the final review of a whole task before its draft merge request. You get the plan summary, every milestone "
    "with its criteria, the project rules digest and the diff of the whole task against its base. Look for what milestone "
    "reviews miss: inconsistencies between milestones, criteria that no longer hold after later changes, dead code left "
    "behind, missing tests across the feature, breaches of project rules. Judge every criterion. Report every actionable "
    "defect in one pass: blocking for wrong behaviour or broken criteria, minor otherwise. verdict is pass when no blocking "
    "finding remains. Do not edit files. Write in the task's language.")
BLIND_RULES = (
    "You check a change blind: you get only its acceptance criteria and its diff, nothing else, and you must not look for "
    "more. Judge each criterion from the diff alone: pass, fail, or unverifiable when the diff cannot show it (that is not a "
    "defect). Report as blocking only defects the diff itself proves. Do not edit files. Write in the task's language.")


def strongest_author(root, key, layout):
    """The strongest model that wrote code in this task; the final reviewers must not be weaker."""
    authors = [layout["develop"]]
    models_by_name = {entry["model"]: entry for entry in layout.values()}
    for event in cards.events(root, key):
        if event.get("type") == "run_finished" and event.get("role") == "develop" and event.get("tier"):
            authors.append({"model": event["model"], "vendor": event.get("vendor"), "tier": event["tier"],
                            "strength": models_by_name.get(event["model"], {}).get("strength", 0)})
    return max(authors, key=models.level)


def criteria(document, behavior=None):
    return [{"milestone": item["id"], "stage": stage["id"], "text": criterion["text"], "check": criterion["check"]}
            for item in document["milestones"] for stage in item["stages"] for criterion in stage["criteria"]
            if behavior is None or (criterion["check"] == "behavior") == behavior]


def browser(root, key, profile, entry, worktree, item, run_role, schema):
    """Browser acceptance of a UI milestone in an isolated preview. Returns the review-shaped result or None."""
    from . import browser_auth, browser_session, isolated_preview
    spec = profile.get("browser")
    if not isinstance(spec, dict) or not isinstance(profile.get("preview"), dict):
        cards.record(root, key, "browser_skipped", milestone=item["id"], reason="no browser or preview in the profile")
        return None
    checks = [c for c in criteria({"milestones": [item]}, behavior=True)]
    with resources.slot(root, "browser", on_wait=lambda name: cards.waiting(root, key, name, item["id"])):
        cards.stop_waiting(root, key, item["id"])
        if spec.get("mode") != "local":
            browser_auth.capture(spec)
        with isolated_preview.open_preview(profile, worktree) as url, tempfile.TemporaryDirectory(prefix="friday-browser-") as empty:
            prompt = (BROWSER_RULES + "\n\nPreview: " + url + "\n\n" + json.dumps(
                {"milestone": {k: item[k] for k in ("id", "title", "goal")}, "criteria": checks,
                 "test_policy": profile.get("behavior_test_policy", "")}, ensure_ascii=False, indent=2))
            mcp = {"name": "friday", "command": [sys.executable, str(ADAPTERS / "browser_mcp.py"), json.dumps(spec), url],
                   "tools": sorted(browser_session.TOOLS)}
            result = run_role(root, key, profile, "browser", entry, empty, prompt, schema, item["id"], mcp=mcp)
    return result


def final_review(root, key, profile, run_role, schema, roles=("final-review", "blind-code")):
    """Final review and blind check of the whole diff, at the same time; returns both structured results."""
    card = cards.read(root, key)
    layout, worktree = card["cast"], card["worktree"]
    document = json.loads((cards.directory(root, key) / "plan.json").read_text(encoding="utf-8"))
    author = strongest_author(root, key, layout)
    available = models.availability(root)
    final_entry, note_final = models.reviewer(profile, layout, "final-review", author, available)
    blind_entry, note_blind = models.reviewer(profile, layout, "blind-code", author, available)
    for note in filter(None, (note_final, note_blind)):
        cards.record(root, key, "cast_note", note=note)
    diff = gate.git(worktree, "diff", card["base"] + "..HEAD").decode("utf-8", "replace")
    full = json.dumps({"summary": document["summary"], "project_rules": document["rules"],
                       "milestones": [{k: m[k] for k in ("id", "title", "goal")} for m in document["milestones"]],
                       "criteria": criteria(document), "owner_decisions": card["decisions"]}, ensure_ascii=False, indent=2)
    with tempfile.TemporaryDirectory(prefix="friday-blind-") as empty, ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {}
        if "final-review" in roles:
            jobs["final-review"] = pool.submit(run_role, root, key, profile, "final-review", final_entry, worktree,
                                               FINAL_RULES + "\n\n" + full + "\n\nDiff of the whole task:\n" + diff, schema, "final-review")
        if "blind-code" in roles:
            jobs["blind-code"] = pool.submit(run_role, root, key, profile, "blind-code", blind_entry, empty,
                                             BLIND_RULES + "\n\nCriteria:\n" + json.dumps(criteria(document, behavior=False),
                                                                                         ensure_ascii=False, indent=2) +
                                             "\n\nDiff:\n" + diff, schema, "blind-code")
        results = {role: job.result() for role, job in jobs.items()}
    for role, result in results.items():
        if result.status != "ok" or not isinstance(result.structured, dict):
            raise FridayError("agent_failed", "%s failed (%s): %s" % (role, result.status, result.error))
        value = result.structured
        cards.record(root, key, "final_reviewed", role=role, verdict=value["verdict"], model=result_model(role, final_entry, blind_entry),
                     blocking=sum(f["severity"] == "blocking" for f in value["findings"]))
    return {role: result.structured for role, result in results.items()}, final_entry


def result_model(role, final_entry, blind_entry):
    return (final_entry if role == "final-review" else blind_entry)["model"]


def description(root, key):
    """The draft merge request text: what was done, what the owner still has to decide, what it cost."""
    from . import status
    card = cards.read(root, key)
    document = json.loads((cards.directory(root, key) / "plan.json").read_text(encoding="utf-8"))
    lines = [document["summary"], "", "## Вехи"]
    for item in card["milestones"]:
        title = next(m["title"] for m in document["milestones"] if m["id"] == item["id"])
        lines.append("- **%s. %s** — %s" % (item["id"], title, {"done": "готово"}.get(item["state"], item["state"])))
        for stage in item.get("stages", []):
            lines.append("  - %s %s" % (stage["id"], (stage.get("commit") or "")[:10]))
    if card["disputes"]:
        lines += ["", "## На решение владельцу"]
        for dispute in card["disputes"]:
            lines.append("- %s `%s`: %s" % (dispute.get("milestone", ""), dispute.get("file", ""), dispute["problem"]))
            lines.append("  Исполнитель: %s" % dispute.get("executor_reason", ""))
    minor = [f for item in card["milestones"] for f in item.get("minor_findings", [])] + card.get("final_minor", [])
    if minor:
        lines += ["", "## Мелкие замечания"] + ["- `%s`: %s" % (f.get("file", ""), f["problem"]) for f in minor]
    outside = sorted({path for item in card["milestones"] for path in item.get("outside_files", [])})
    if outside:
        lines += ["", "## Файлы вне плана", ", ".join("`%s`" % path for path in outside)]
    unverified = [event["milestone"] for event in cards.events(root, key) if event.get("type") in ("browser_unverifiable", "browser_skipped")]
    if unverified:
        lines += ["", "## Не проверено в браузере", ", ".join(sorted(set(unverified)))]
    spent = status.totals(cards.events(root, key))
    lines += ["", "---", "Friday: %s, %s токенов, %d запусков моделей. Раскладка: %s." % (
        status.money(spent), status.tokens(spent["tokens"]), spent["runs"],
        ", ".join("%s → %s" % (role, entry["model"]) for role, entry in card["cast"].items()))]
    return "\n".join(lines)


def git_command(repo, *args, timeout=300):
    result = subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise FridayError("delivery_failed", result.stderr.strip() or "git " + args[0] + " failed")
    return result.stdout.strip()


def deliver(root, key, profile):
    """Push the task branch and open a draft MR/PR, once; without a configured transport only the text is saved."""
    card = cards.read(root, key)
    settings = profile.get("delivery") or {"transport": "none"}
    text = description(root, key)
    (cards.directory(root, key) / "merge-request.md").write_text(text + "\n", encoding="utf-8")
    summary = json.loads((cards.directory(root, key) / "plan.json").read_text(encoding="utf-8"))["summary"]
    first = summary.split(". ")[0].strip().rstrip(".")
    title = "%s: %s" % (key, first if len(first) <= 90 else first[:89] + "…")
    if settings.get("transport", "none") == "none":
        cards.record(root, key, "delivery_skipped", reason="no transport in the profile")
        return {"transport": "none", "description": str(cards.directory(root, key) / "merge-request.md")}
    target = settings.get("target_branch") or profile.get("base_branch")
    if not isinstance(target, str) or not target:
        raise FridayError("invalid_profile", "delivery.target_branch is required.")
    worktree, branch = card["worktree"], card["branch"]
    head = git_command(worktree, "rev-parse", "HEAD")
    delivered = card.get("delivery") or {}
    remote = git_command(worktree, "ls-remote", "origin", "refs/heads/" + branch).split()
    observed = remote[0] if remote else ""
    if observed and observed != delivered.get("head"):
        raise FridayError("remote_conflict", "Remote branch %s exists and was not pushed by this task." % branch)
    if observed != head:
        git_command(worktree, "push", "--porcelain", "--force-with-lease=refs/heads/%s:%s" % (branch, observed), "origin",
                    head + ":refs/heads/" + branch)
    cards.update(root, key, lambda value: value.update(delivery=dict(delivered, head=head, branch=branch)))
    if delivered.get("url"):
        return dict(delivered, head=head)
    if settings["transport"] == "gitlab":
        from . import mission
        existing = mission.matching_mrs(profile["gitlab"], branch)
        created = existing[0] if existing else mission.create_mr(profile, worktree, branch, target, title, text)
        url = created.get("web_url") or created.get("url", "")
    elif settings["transport"] == "github":
        from . import github
        existing = github.matching_pulls(profile, branch)
        created = existing[0] if existing else github.create_pull(profile, branch, target, title, text)
        url = created.get("web_url") or created.get("url", "")
    else:
        raise FridayError("invalid_profile", "Unknown delivery transport: " + str(settings["transport"]))
    cards.update(root, key, lambda value: value.update(delivery=dict(value.get("delivery") or {}, url=url, iid=created.get("iid"))))
    cards.record(root, key, "draft_opened", url=url, head=head)
    return {"transport": settings["transport"], "url": url, "head": head}
