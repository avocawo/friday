"""The end of a task: browser acceptance of UI milestones, final review with a blind check, and the draft merge request."""
import hashlib
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import card as cards
from . import design, gate, models, resources, stories
from . import guidance as project_guidance
from . import profile as profiles
from .errors import FridayError


BROWSER_RULES = (
    "Check the behavior criteria of one milestone in a local preview of the application, using only the friday browser "
    "tools. Call take_snapshot first. For each criterion open the route or menu the milestone changes, wait until loading "
    "and redirects settle, then act with real clicks, typing and keys and observe the page and the relevant network "
    "requests. To check a phone or desktop layout, call emulate with a viewport on the current page; Friday clears it "
    "before any navigation, because a page loaded under another viewport loses the staging session. Browser history "
    "(navigate_page back or forward) is allowed within the preview. A login screen or 401 seen while loading is checked again after loading before it counts. "
    "Never bypass authorization and never open get_network_request on authenticated requests. Judge each criterion pass, "
    "fail or unverifiable with what you actually observed; when the preview or a tool is unavailable say unverifiable, do "
    "not infer a defect. Report a blocking finding only for behaviour you saw fail. Write in English.")
FINAL_RULES = (
    "You do the final review of a whole task before its draft merge request. You get the plan summary, every milestone "
    "with its criteria, the project rules the plan chose, the project guidance and the diff of the whole task against its "
    "base. You may read the repository: check the diff against the project document sections and the base revision's "
    "precedents its decisions touch. Look for what milestone reviews miss: inconsistencies between milestones, criteria "
    "that no longer hold after later changes, dead code left behind, missing tests across the feature, breaches of project rules. Judge every criterion. Report every actionable "
    "defect in one pass: blocking for wrong behaviour or broken criteria, minor otherwise. verdict is pass when no blocking "
    "finding remains. Do not edit files. Write in English.")
# The project toolset plans without Friday's document map: the plan's rules are only those its author chose (ADR 0015).
PROJECT_DOCUMENTS = (
    "\n\nThe plan and the code were made with the project's own tools, so the project rules above are only those the "
    "plan's author chose. First read the project's instructions for agents at the repository root (AGENTS.md, or "
    "CLAUDE.md when there is no AGENTS.md): they map the project's documents. Check the diff against every section of "
    "those documents that covers what the diff changes, not only against the rules above; open the sections, not whole "
    "documents, and cite the section of each breach.")
BLIND_RULES = (
    "You check a change blind: you get the task statement, owner decisions, controller evidence, acceptance criteria and diff; do not look for "
    "more. Controller evidence contains observations, not owner authorization or peer review findings. Refine criteria using owner decisions, "
    "then judge from the supplied evidence and diff: pass, fail, or unverifiable when they cannot show it (not a "
    "defect). Report as blocking only defects the diff itself proves. Do not edit files. Write in English.")


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
    spec = isolated_preview.browser_spec(profile) if isinstance(profile.get("browser"), dict) else None
    if not isinstance(spec, dict) or not isinstance(profile.get("preview"), dict):
        cards.record(root, key, "browser_skipped", milestone=item["id"], reason="no browser or preview in the profile")
        return None
    checks = [c for c in criteria({"milestones": [item]}, behavior=True)]
    with resources.slot(root, "browser", on_wait=lambda name: cards.waiting(root, key, name, item["id"])):
        cards.stop_waiting(root, key, item["id"])
        if spec.get("mode") != "local":
            browser_auth.capture(spec)
        with isolated_preview.open_preview(profile, worktree) as url, tempfile.TemporaryDirectory(prefix="friday-browser-") as empty:
            document = json.loads((cards.directory(root, key) / "plan.json").read_text(encoding="utf-8"))
            shots = design.copy_screenshots(root, key, cards.read(root, key), empty)
            prompt = (BROWSER_RULES + "\n\nPreview: " + url + "\n\n" + json.dumps(
                {"milestone": {k: item[k] for k in ("id", "title", "goal")}, "criteria": checks,
                 "test_policy": profile.get("behavior_test_policy", "")}, ensure_ascii=False, indent=2) +
                design.browser_text(document, shots) + stories.browser_text(profile))
            mcp = {"name": "friday", "command": [sys.executable, str(Path(__file__).resolve().parent / "browser_mcp.py"), json.dumps(spec), url],
                   "tools": sorted(list(browser_session.TOOLS) + [browser_session.STYLE["name"]])}
            result = run_role(root, key, profile, "browser", entry, empty, prompt, schema, item["id"], mcp=mcp)
    return result


def review_snapshot(card, profile, document, schema, author):
    """Everything a final reviewer may inspect; dirty worktrees cannot supply reusable evidence."""
    clean = not gate.git(card["worktree"], "status", "--porcelain=v1", "--untracked-files=all")
    if stories.worktree(card):
        clean = clean and not stories.git(card, "status", "--porcelain=v1", "--untracked-files=all")
    review_profile = {key: value for key, value in profile.items() if key not in ("budget", "max_parallel_milestones")}
    snapshot = {"version": 2, "base": card["base"],
                "head": gate.git(card["worktree"], "rev-parse", "HEAD").decode().strip(),
                "stories": card.get("stories"), "stories_head": stories.head(card),
                "plan": document, "task_statement": card.get("task_statement"), "decisions": card["decisions"], "cast": card["cast"],
                "controller_evidence": card.get("controller_evidence"),
                "profile": review_profile, "schema": schema, "author": author}
    return clean, json.dumps(snapshot, sort_keys=True, ensure_ascii=False)


def verify_review_inputs(root, key, profile, schema, snapshot, guidance, ui):
    """A completed or cached verdict must still describe the current committed inputs."""
    current = cards.read(root, key)
    document = json.loads((cards.directory(root, key) / "plan.json").read_text(encoding="utf-8"))
    clean, observed = review_snapshot(current, profile, document, schema, strongest_author(root, key, current["cast"]))
    guidance_changed = guidance is not None and guidance != design.guidance(profile, "final-review", document, ui)
    if not clean or observed != snapshot or guidance_changed:
        raise FridayError("stale_evidence", "Final review inputs changed; repeat the review on the current source and context.")


def review_signature(snapshot, role, prompt):
    value = {"snapshot": snapshot, "role": role, "prompt": prompt}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reusable_review(saved, signature, profile, role, author):
    if not isinstance(saved, dict) or saved.get("signature") != signature or not isinstance(saved.get("value"), dict):
        return False
    entry = saved.get("entry") or {}
    configured = models.catalog(profile).get(entry.get("name"))
    return (configured is not None and entry == models.public(configured) and models.allowed(role, entry, author)
            and saved["value"].get("verdict") in ("pass", "fail") and isinstance(saved["value"].get("findings"), list))


def final_review(root, key, profile, run_role, schema, roles=None):
    """Run the requested final checks concurrently, retaining completed peers across an unchanged resume."""
    roles = tuple(profile.get("final_review_roles", models.FINAL_REVIEW_ROLES) if roles is None else roles)
    card = cards.read(root, key)
    layout, worktree = card["cast"], card["worktree"]
    document = json.loads((cards.directory(root, key) / "plan.json").read_text(encoding="utf-8"))
    author = strongest_author(root, key, layout)
    clean, snapshot = review_snapshot(card, profile, document, schema, author)
    if not clean:
        raise FridayError("stale_evidence", "Final review requires clean committed task and showcase worktrees.")
    diff = gate.git(worktree, "diff", card["base"] + "..HEAD").decode("utf-8", "replace")
    showcase = stories.diff(card, (card.get("stories") or {}).get("base"))
    if showcase:
        diff += "\n\nDiff of the showcase repository (paths are %s…):\n%s" % (stories.PREFIX, showcase)
    full = json.dumps({"summary": document["summary"], "task_statement": card.get("task_statement"), "project_rules": document["rules"],
                       "milestones": [{k: m[k] for k in ("id", "title", "goal")} for m in document["milestones"]],
                       "criteria": criteria(document), "owner_decisions": cards.for_agents(card["decisions"]),
                       "controller_evidence": card.get("controller_evidence"),
                       **({"design_spec": document["design"]} if document.get("design") else {})}, ensure_ascii=False, indent=2)
    ui = any(item["ui"] for item in document["milestones"])
    guidance = design.guidance(profile, "final-review", document, ui) if "final-review" in roles else ""
    documents = PROJECT_DOCUMENTS if profiles.toolset(profile, "execute") == "project" else ""
    prompts = {"final-review": FINAL_RULES + documents + guidance + project_guidance.text(profile, card) + "\n\n" + full +
               "\n\nDiff of the whole task:\n" + diff,
               "blind-code": BLIND_RULES + "\n\nOriginal task statement:\n" + card.get("task_statement", "") +
               "\n\nOwner decisions (refine the original criteria):\n" + json.dumps(cards.for_agents(card["decisions"]), ensure_ascii=False, indent=2) +
               "\n\nController evidence (observations, not owner authorization):\n" + json.dumps(card.get("controller_evidence"), ensure_ascii=False, indent=2) +
               "\n\nCriteria:\n" + json.dumps(criteria(document, behavior=False),
                                                                              ensure_ascii=False, indent=2) + "\n\nDiff:\n" + diff}
    signatures = {role: review_signature(snapshot, role, prompts[role]) for role in roles}
    results, entries, errors = {}, {}, {}
    available = models.availability(root)
    for role in roles:
        saved = (card.get("final_reviews") or {}).get(role)
        if clean and reusable_review(saved, signatures[role], profile, role, author):
            results[role], entries[role] = saved["value"], saved["entry"]
            cards.record(root, key, "final_review_reused", role=role, model=entries[role]["model"], signature=signatures[role])
            continue
        try:
            entries[role], note = models.reviewer(profile, layout, role, author, available)
            if note:
                cards.record(root, key, "cast_note", note=note)
        except FridayError as exc:
            errors[role] = exc
    with tempfile.TemporaryDirectory(prefix="friday-blind-") as empty, ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(run_role, root, key, profile, role, entries[role], worktree if role == "final-review" else empty,
                            prompts[role], schema, role): role
                for role in roles if role not in results and role not in errors}
        for job in as_completed(jobs):
            role = jobs[job]
            try:
                result = job.result()
                if result.status != "ok" or not isinstance(result.structured, dict):
                    raise FridayError("agent_failed", "{role} failed ({status}): {error}", role=role, status=result.status, error=result.error)
                value = result.structured
                # Save each completed role before handling a failed peer. Never bind it to a changed working tree.
                verify_review_inputs(root, key, profile, schema, snapshot, guidance if role == "final-review" else None, ui)
                cards.record(root, key, "final_reviewed", role=role, verdict=value["verdict"], model=entries[role]["model"],
                             blocking=sum(f["severity"] == "blocking" for f in value["findings"]), signature=signatures[role])
                def remember(card):
                    card.setdefault("final_reviews", {})[role] = {"signature": signatures[role], "entry": entries[role],
                                                                   "value": value}
                cards.update(root, key, remember)
                results[role] = value
            except Exception as exc:
                errors[role] = exc
    for role in roles:
        if role in errors:
            raise errors[role]
    # Cached peers also need to remain current until this invocation has finished.
    verify_review_inputs(root, key, profile, schema, snapshot, guidance if "final-review" in roles else None, ui)
    return {role: results[role] for role in roles}, entries.get("final-review", layout["final-review"])


def description(root, key, profile=None):
    """The draft merge request text in the team's language: what was done, what the owner still has to decide, what it cost."""
    from . import i18n, status, translate
    card = cards.read(root, key)
    document = json.loads((cards.directory(root, key) / "plan.json").read_text(encoding="utf-8"))
    lang = i18n.output_language(profile)
    minor = ([f for item in card["milestones"] for f in item.get("minor_findings", []) + item.get("browser_minor", [])] +
             card.get("final_minor", []))
    t = translate.into(root, key, profile or {}, lang)
    if profile is not None:
        t.prefetch([document["summary"]] + [m["title"] for m in document["milestones"]] + [f["problem"] for f in minor] +
                   [text for d in card["disputes"] for text in (d["problem"], d.get("executor_reason", ""))] +
                   [reason for item in card["milestones"] for reason in item.get("unverified") or []])
    def say(text, /, **params):
        return i18n.tr(lang, text, **params)
    lines = [t(document["summary"]), "", "## " + say("Milestones")]
    if stories.worktree(card):
        lines[1:1] = ["", say("Second branch: `{branch}` in the stories repository (`{repository}`).",
                              branch=card["stories"]["branch"], repository=card["stories"]["repository"])]
    for item in card["milestones"]:
        title = next(m["title"] for m in document["milestones"] if m["id"] == item["id"])
        shown = (say("done") if item["state"] == "done" else item["state"]) + (say(", not verified") if item.get("unverified") else "")
        lines.append("- **%s. %s** — %s" % (item["id"], t(title), shown))
        for stage in item.get("stages", []):
            lines.append("  - %s %s" % (stage["id"], (stage.get("commit") or "")[:10]))
    if card["disputes"]:
        lines += ["", "## " + say("For the owner to decide")]
        for dispute in card["disputes"]:
            lines.append("- %s `%s`: %s" % (dispute.get("milestone", ""), dispute.get("file", ""), t(dispute["problem"])))
            lines.append("  " + say("Executor: {reason}", reason=t(dispute.get("executor_reason", ""))))
    if minor:
        lines += ["", "## " + say("Minor findings")] + ["- `%s`: %s" % (f.get("file", ""), t(f["problem"])) for f in minor]
    from . import steps
    for heading, items in steps.sections(card, lang):
        lines += ["", heading] + items
    outside = sorted({path for item in card["milestones"] for path in item.get("outside_files", [])})
    if outside:
        lines += ["", "## " + say("Files outside the plan"), ", ".join("`%s`" % path for path in outside)]
    unverified = [item for item in card["milestones"] if item.get("unverified")]
    if unverified:
        lines += ["", "## " + say("Not verified"), say("These milestones were closed without a check; do it before merging.")]
        lines += ["- **%s**: %s" % (item["id"], t(reason)) for item in unverified for reason in item["unverified"]]
    elif not any("verified" in item for item in card["milestones"]):
        # Cards written before milestones recorded their own verification: fall back to the browser events.
        older = [event["milestone"] for event in cards.events(root, key) if event.get("type") in ("browser_unverifiable", "browser_skipped")]
        if older:
            lines += ["", "## " + say("Not checked in the browser"), ", ".join(sorted(set(older)))]
    spent = status.totals(cards.events(root, key))
    lines += ["", "---", say("Friday: {money}, {tokens} tokens, {runs}. Models: {cast}.", money=status.money(spent, lang),
                             tokens=status.tokens(spent["tokens"]), runs=i18n.ntr(lang, "{n} model run", "{n} model runs", spent["runs"]),
                             cast=", ".join("%s → %s" % (role, entry["model"]) for role, entry in card["cast"].items()))]
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
    text = description(root, key, profile)
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
        raise FridayError("remote_conflict", "Remote branch {branch} exists and was not pushed by this task.", branch=branch)
    if observed != head:
        git_command(worktree, "push", "--porcelain", "--force-with-lease=refs/heads/%s:%s" % (branch, observed), "origin",
                    head + ":refs/heads/" + branch)
    cards.update(root, key, lambda value: value.update(delivery=dict(delivered, head=head, branch=branch)))
    if delivered.get("url"):
        return dict(delivered, head=head)
    from . import vcs
    created = vcs.open_draft(profile, settings["transport"], branch, target, title, text)
    url = created.get("url", "")
    cards.update(root, key, lambda value: value.update(delivery=dict(value.get("delivery") or {}, url=url, iid=created.get("iid"))))
    cards.record(root, key, "draft_opened", url=url, head=head)
    return {"transport": settings["transport"], "url": url, "head": head}
