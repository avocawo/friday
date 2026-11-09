"""The readable history of a task, built from its card and events (ADR 0006).

The card is the only source of truth; journals are renderings of it. A profile lists where they go:
"journal": [{"kind": "markdown", "dir": "...", "template": "..."}, {"kind": "jsonl", "dir": "..."}]
("journal_dir": "..." is short for one markdown journal). Obsidian is a markdown journal in a vault folder."""
import datetime
import json
import os
import re
import sys
from pathlib import Path

from . import card as cards
from . import profile as profiles
from . import i18n, state, status, translate
from .errors import FridayError
from .i18n import N_

TEMPLATES = Path(__file__).resolve().parent / "templates"
MARKER = "<!-- friday:generated %s -->"
FIELD = "friday_generated: %s"
NOTES = re.compile(r"<!-- friday:notes -->\n(.*?)<!-- /friday:notes -->", re.S)
# Events that change what the journal shows; anything else waits for the next of these.
SIGNIFICANT = {"plan_ready", "plan_approved", "milestone_done", "milestone_failed", "decision", "final_reviewed",
               "draft_opened", "delivery_skipped", "run_task_finished", "stopped", "start_failed", "resumed",
               "replan_started", "replan_failed"}
STATES = {"planning": N_("Planning"), "awaiting_approval": N_("Plan awaiting approval"), "approved": N_("Plan approved"),
          "running": N_("Running"), "stopped": N_("Stopped"), "done": N_("Done"), "failed": N_("Failed")}
MILESTONE_STATES = {"pending": N_("ahead"), "running": N_("running"), "done": N_("done"), "failed": N_("failed"),
                    "reopened": N_("reopened"), "blocked": N_("environment unavailable")}
# Headings of the built-in templates; a project's own template may use them as {{h_<name>}}.
HEADINGS = {"plan": N_("Plan"), "milestones": N_("Milestones"), "disputes": N_("For the owner to decide"),
            "decisions": N_("Owner decisions"), "costs": N_("Spending"), "timeline": N_("Timeline"), "notes": N_("Notes"),
            "now": N_("Friday — now")}


def sinks(profile):
    value = profile.get("journal")
    if value is None and profile.get("journal_dir"):
        value = [{"kind": "markdown", "dir": profile["journal_dir"]}]
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) or item.get("kind") not in ("markdown", "jsonl")
                                          or not isinstance(item.get("dir"), str) for item in value):
        raise FridayError("invalid_profile", "journal must list sinks with kind markdown or jsonl and a dir.")
    base = Path(profile.get("path", ".")).parent
    return [dict(item, dir=str((base / Path(item["dir"]).expanduser()).resolve())) for item in value]


def template(sink, profile, name):
    """The sink's own template, then the project's .friday/<name>, then Friday's built-in one."""
    candidates = []
    if sink.get(name.split(".")[0] + "_template") or (name == "task.md" and sink.get("template")):
        own = sink.get(name.split(".")[0] + "_template") or sink.get("template")
        candidates.append((Path(profile.get("path", ".")).parent / Path(own).expanduser()).resolve())
    candidates += [Path(profile["repository"]) / ".friday" / name, TEMPLATES / name]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise FridayError("not_found", "No journal template {name}", name=name)


def fill(text, values):
    return re.sub(r"\{\{(\w+)\}\}", lambda match: str(values.get(match.group(1), "")), text)


def owned(key, text):
    """Mark a page as Friday's: a front matter field (Obsidian needs front matter on the first line), else a comment."""
    text = text.lstrip("\n")
    if text.startswith("---\n"):
        return "---\n" + FIELD % key + "\n" + text[4:]
    return MARKER % key + "\n" + text


def is_owned(key, text):
    head = text.split("\n", 12)[:12]
    return (MARKER % key) in head or (FIELD % key) in head


def or_dash(lines):
    return "\n".join(lines) if lines else "—"


def local_time(stamp):
    try:
        moment = datetime.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return str(stamp)
    return moment.astimezone().strftime("%d.%m %H:%M")


def describe(event, lang="en", t=lambda text: text):
    """One line of history in plain words, or None for events that only matter to Friday.
    Fixed words come from the catalogs; t translates text that came from an agent or an error."""
    kind, milestone = event["type"], event.get("milestone") or ""
    def say(text, /, **params):
        return i18n.tr(lang, text, **params)
    texts = {
        "created": lambda: say("Task created."),
        "worktree_created": lambda: say("Branch `{branch}` from `{base}`.", branch=event.get("branch", ""), base=(event.get("base") or "")[:10]),
        "plan_invalid": lambda: say("The plan failed the feasibility checks and went back for revision ({count} problems).",
                                    count=len(event.get("errors", []))),
        "plan_reviewed": lambda: say("Plan reviewed: {verdict}, {findings} findings, {blocking} blocking.", verdict=event.get("verdict"),
                                     findings=event.get("findings"), blocking=event.get("blocking")),
        "plan_ready": lambda: say("Plan ready: {milestones} milestones, {notes} open review notes.", milestones=event.get("milestones"),
                                  notes=event.get("open_notes")),
        "plan_approved": lambda: say("Plan approved automatically.") if event.get("by") == "auto" else say("Plan approved."),
        "cast_note": lambda: say("Models: {note}", note=t(event.get("note", ""))),
        "milestone_started": lambda: say("{milestone}: started.", milestone=milestone),
        "gate": lambda: say("{milestone}: gate passed.", milestone=milestone) if event.get("status") == "passed" else
                        say("{milestone}: gate failed.", milestone=milestone) if event.get("status") == "failed" else
                        say("{milestone}: gate {status}.", milestone=milestone, status=event.get("status")),
        "milestone_reviewed": lambda: say("{milestone}: review by {model} — {verdict}, {blocking} blocking.", milestone=milestone,
                                          model=event.get("model"), verdict=event.get("verdict"), blocking=event.get("blocking")),
        "browser_checked": lambda: say("{milestone}: browser acceptance by {model} — {verdict}.", milestone=milestone,
                                       model=event.get("model"), verdict=event.get("verdict")),
        "browser_unverifiable": lambda: say("{milestone}: could not be checked in the browser ({reason}).", milestone=milestone,
                                            reason=t(event.get("reason", ""))),
        "browser_skipped": lambda: say("{milestone}: no browser acceptance ({reason}).", milestone=milestone, reason=t(event.get("reason", ""))),
        "milestone_done": lambda: say("{milestone}: done in {rounds} round(s), {commits} commits, {disputes} disputed.", milestone=milestone,
                                      rounds=event.get("rounds"), commits=event.get("commits"), disputes=event.get("disputes")),
        "milestone_failed": lambda: say("{milestone}: failed ({reason}).", milestone=milestone,
                                        reason=t(event.get("reason") or "") or say("the gate never passed")),
        "parallel_started": lambda: say("In parallel: {milestones}.", milestones=", ".join(map(str, event.get("milestones") or []))),
        "decision": lambda: say("Owner decision «{name}», reopened: {reopened}.", name=event.get("name"),
                                reopened=", ".join(event.get("reopened", [])) or say("nothing")),
        "final_reviewed": lambda: say("Final, {role} ({model}): {verdict}, {blocking} blocking.",
                                      role=status.role_name(event.get("role"), lang), model=event.get("model"),
                                      verdict=event.get("verdict"), blocking=event.get("blocking")),
        "draft_opened": lambda: say("Draft MR: {url}", url=event.get("url", "")),
        "delivery_skipped": lambda: say("The MR is not published: the profile has no transport; its text is saved."),
        "stopped": lambda: say("Stopped."),
        "resumed": lambda: say("Resumed."),
        "start_failed": lambda: say("Preparation failed: {error}", error=t(event.get("error", ""))),
        "replan_started": lambda: say("Plan {revision} sent back to the planner with the owner's decisions: {decisions}.",
                                      revision=event.get("revision"), decisions=", ".join(event.get("decisions", []))),
        "replan_failed": lambda: say("Re-planning failed, the previous plan still waits for approval: {error}",
                                     error=t(event.get("error", ""))),
    }
    if kind == "run_finished" and event.get("status") not in ("ok", None):
        return "%s %s (%s): %s %s" % (milestone, event.get("role"), event.get("model"), event.get("status"), t((event.get("error") or "")[:160]))
    text = texts.get(kind)
    return text() if text else None


def free_text(card, document, events):
    """Everything agents or errors wrote that the journal shows: the texts to translate in one batch."""
    texts = [document.get("summary", "")] + card["plan"].get("questions", []) + card["plan"].get("overlaps", [])
    texts += document.get("assumptions", [])
    for note in card["plan"].get("review_notes", []):
        texts += [note["problem"], note["answer"]]
    for item in document.get("milestones", []):
        texts += [item.get("title", ""), item.get("goal", "")]
    for item in card["milestones"]:
        texts += [item.get("summary", "")] + [finding["problem"] for finding in item.get("minor_findings", [])]
    for dispute in card["disputes"]:
        texts += [dispute["problem"], dispute.get("executor_reason", "")]
    for event in events:
        texts += [event.get(name) for name in ("note", "reason", "error") if isinstance(event.get(name), str)]
        if event.get("error"):
            texts.append(event["error"][:160])
    return [text for text in texts if text]


def render_task(root, key, profile, sink, previous=""):
    card = cards.read(root, key)
    plan_path = cards.directory(root, key) / "plan.json"
    document = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.is_file() else {}
    events = cards.events(root, key)
    lang = i18n.output_language(profile)
    t = translate.into(root, key, profile, lang)
    t.prefetch(free_text(card, document, events))
    def say(text, /, **params):
        return i18n.tr(lang, text, **params)
    spent = status.totals(events)
    titles = {item["id"]: item for item in document.get("milestones", [])}
    plan = []
    if document:
        plan.append(t(document.get("summary", "")))
        plan += [""] + [say("- Question: {text}", text=t(item)) for item in card["plan"].get("questions", [])]
        for note in card["plan"].get("review_notes", []):
            plan.append(say("- Open review note ({milestone}/{stage}): {problem} Planner's answer: {answer}", milestone=note["milestone"],
                            stage=note["stage"], problem=t(note["problem"]), answer=t(note["answer"])))
        plan += [say("- Overlap: {text}", text=t(item)) for item in card["plan"].get("overlaps", [])]
        plan += [say("- Assumption: {text}", text=t(item)) for item in document.get("assumptions", [])]
    milestones = []
    for item in card["milestones"]:
        planned = titles.get(item["id"], {})
        state_text = say(MILESTONE_STATES[item["state"]]) if item["state"] in MILESTONE_STATES else item["state"]
        milestones.append("### %s. %s — %s" % (item["id"], t(planned.get("title", item.get("title", ""))), state_text))
        if planned.get("goal"):
            milestones.append(t(planned["goal"]))
        if item.get("summary"):
            milestones.append("\n> " + say("Executor ({model}): {summary}", model=item.get("executor", ""),
                                            summary=t(item["summary"]).replace("\n", "\n> ")))
        for stage in item.get("stages", []):
            if stage.get("commit"):
                milestones.append("- %s `%s`" % (stage["id"], stage["commit"][:10]))
        for finding in item.get("minor_findings", []):
            milestones.append(say("- Minor: `{file}` {problem}", file=finding.get("file", ""), problem=t(finding["problem"])))
        if item.get("outside_files"):
            milestones.append(say("- Outside the plan: {files}", files=", ".join("`%s`" % path for path in item["outside_files"])))
        milestones.append("")
    disputes = [say("- **{milestone}** `{file}`: {problem}\n  - Reviewer {reviewer}; executor: {reason}", milestone=d.get("milestone", ""),
                    file=d.get("file", ""), problem=t(d["problem"]), reviewer=d.get("reviewer", ""), reason=t(d.get("executor_reason", "")))
                for d in card["disputes"]]
    decisions = ["- **%s**: %s%s" % (d["name"], d["text"], " " + say("(reopened: {milestones})", milestones=", ".join(d["reopen"]))
                                     if d.get("reopen") else "") for d in card["decisions"]]
    costs = [say("{money}, {tokens} tokens, {runs}, {seconds} of model work.", money=status.money(spent, lang),
                 tokens=status.tokens(spent["tokens"]), runs=i18n.ntr(lang, "{n} run", "{n} runs", spent["runs"]),
                 seconds=status.duration(spent["seconds"], lang)), ""]
    costs += ["| %s | %s | %s | %s |" % (say("Role"), say("Runs"), say("Tokens"), say("Spent")), "|---|---|---|---|"]
    costs += ["| %s | %d | %s | %s |" % (status.role_name(role, lang), value["runs"], status.tokens(value["tokens"]),
                                       status.money({"cost_usd": value["cost_usd"], "unpriced_runs": 0}, lang))
              for role, value in sorted(spent["by_role"].items(), key=lambda item: -item[1]["tokens"])]
    timeline = ["- %s %s" % (local_time(event["at"]), line) for event in events for line in [describe(event, lang, t)] if line]
    found = NOTES.search(previous)
    notes = "<!-- friday:notes -->\n%s<!-- /friday:notes -->" % (found.group(1) if found else "")
    delivery = card.get("delivery") or {}
    status_line = say(STATES[card["state"]]) if card["state"] in STATES else card["state"]
    if delivery.get("url"):
        status_line += " · [%s](%s)" % (say("draft MR"), delivery["url"])
    if card.get("branch"):
        status_line += " · " + say("branch `{branch}`", branch=card["branch"])
    title = t(document.get("summary", "")).split(". ")[0].rstrip(".")[:120] if document else ""
    values = {"key": key, "state": card["state"], "updated": card["updated_at"], "cost": "%.4f" % spent["cost_usd"],
              "title": title or key, "status": status_line, "plan": or_dash(plan), "milestones": or_dash(milestones).rstrip(),
              "disputes": or_dash(disputes), "decisions": or_dash(decisions), "costs": "\n".join(costs),
              "timeline": or_dash(timeline), "notes": notes}
    values.update(headings(lang))
    return owned(key, fill(template(sink, profile, "task.md"), values))


def headings(lang):
    return {"h_" + name: i18n.tr(lang, text) for name, text in HEADINGS.items()}


def render_now(root, sink, profile):
    lang = i18n.output_language(profile)
    say = lambda text: i18n.tr(lang, text)
    rows = ["| %s | %s | %s | %s |" % (say("Task"), say("State"), say("Now"), say("Spent")), "|---|---|---|---|"]
    for row in status.overview(root):
        doing = "; ".join(status.describe(now) for now in row["now"]) or "; ".join(status.describe_wait(w) for w in row["waiting"]) or "—"
        rows.append("| [[%s]] | %s | %s | %s |" % (row["key"], say(STATES[row["state"]]) if row["state"] in STATES else row["state"],
                                                  doing.replace("|", "/"), status.money(row["spent"], lang)))
    return owned("now", fill(template(sink, profile, "now.md"), dict(headings(lang), updated=state.now(), tasks="\n".join(rows))))


def write_page(path, key, text):
    """Replace a page Friday wrote before; never overwrite a page somebody else wrote."""
    if path.exists() and not is_owned(key, path.read_text(encoding="utf-8")):
        raise FridayError("journal_conflict", "{path} exists and was not written by Friday; it is left untouched.", path=path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def sync(root, key, profile=None):
    """Render the task into every journal its profile names; returns the written paths."""
    profile = profile or profiles.load(cards.read(root, key)["profile"])
    written = []
    for sink in sinks(profile):
        directory = Path(sink["dir"])
        if sink["kind"] == "markdown":
            page = directory / (key + ".md")
            previous = page.read_text(encoding="utf-8") if page.exists() else ""
            write_page(page, key, render_task(root, key, profile, sink, previous))
            write_page(directory / (i18n.tr(i18n.output_language(profile), HEADINGS["now"]) + ".md"), "now", render_now(root, sink, profile))
            written.append(str(page))
        else:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / (key + ".jsonl")
            temporary = target.with_name("." + target.name + ".tmp")
            temporary.write_text("".join(json.dumps(dict(event, task=key), ensure_ascii=False) + "\n" for event in cards.events(root, key)),
                                 encoding="utf-8")
            os.replace(temporary, target)
            written.append(str(target))
    return written


def on_event(root, key, event):
    """Called after each card event: keep journals current without ever breaking the pipeline."""
    if event["type"] not in SIGNIFICANT:
        return
    try:
        source = cards.read(root, key)["profile"]
        if not Path(source).is_file():
            return
        sync(root, key, profiles.load(source))
    except Exception as exc:  # a journal must never stop the pipeline
        print(i18n._("Friday: journal {key} not updated: {error}", key=key, error=i18n.error(exc)), file=sys.stderr)
