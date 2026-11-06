"""Make a task plan: planner, deterministic feasibility checks, review by another model, one revision round."""
import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from . import card as cards
from . import models, repomap, runner
from . import profile as profiles
from .errors import FridayError

LIMITS = {"stage_files": 10, "stage_lines": 500}
ACTIVE = ("awaiting_approval", "approved", "running")

STRINGS = {"type": "array", "items": {"type": "string"}}
CRITERION = {"type": "object", "additionalProperties": False, "required": ["text", "check"],
             "properties": {"text": {"type": "string"}, "check": {"type": "string", "enum": ["code", "behavior"]}}}
STAGE = {"type": "object", "additionalProperties": False,
         "required": ["id", "brief", "steps", "files", "new_files", "estimate_lines", "criteria"],
         "properties": {"id": {"type": "string"}, "brief": {"type": "string"}, "steps": STRINGS, "files": STRINGS,
                        "new_files": STRINGS, "estimate_lines": {"type": "integer"},
                        "criteria": {"type": "array", "items": CRITERION}}}
MILESTONE = {"type": "object", "additionalProperties": False,
             "required": ["id", "title", "goal", "complexity", "ui", "parallel", "depends_on", "files", "stages"],
             "properties": {"id": {"type": "string"}, "title": {"type": "string"}, "goal": {"type": "string"},
                            "complexity": {"type": "string", "enum": ["clear", "complex"]}, "ui": {"type": "boolean"},
                            "parallel": {"type": "boolean"}, "depends_on": STRINGS, "files": STRINGS,
                            "stages": {"type": "array", "items": STAGE}}}
RESPONSE = {"type": "object", "additionalProperties": False, "required": ["finding", "accepted", "reason"],
            "properties": {"finding": {"type": "integer"}, "accepted": {"type": "boolean"}, "reason": {"type": "string"}}}
PLAN_SCHEMA = {"type": "object", "additionalProperties": False,
               "required": ["summary", "rules", "assumptions", "questions", "milestones", "responses"],
               "properties": {"summary": {"type": "string"}, "rules": STRINGS, "assumptions": STRINGS, "questions": STRINGS,
                              "milestones": {"type": "array", "items": MILESTONE},
                              "responses": {"type": "array", "items": RESPONSE}}}
FINDING = {"type": "object", "additionalProperties": False, "required": ["milestone", "stage", "severity", "problem", "suggestion"],
           "properties": {"milestone": {"type": "string"}, "stage": {"type": "string"},
                          "severity": {"type": "string", "enum": ["blocking", "minor"]},
                          "problem": {"type": "string"}, "suggestion": {"type": "string"}}}
REVIEW_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "findings"],
                 "properties": {"verdict": {"type": "string", "enum": ["ok", "changes"]},
                                "findings": {"type": "array", "items": FINDING}}}
DOCUMENT = {"type": "object", "additionalProperties": False, "required": ["path", "covers"],
            "properties": {"path": {"type": "string"}, "covers": {"type": "string"}}}
DIGEST_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["rules", "documents"],
                 "properties": {"rules": STRINGS, "documents": {"type": "array", "items": DOCUMENT}}}
RULES_DOCS = ["AGENTS.md", "CLAUDE.md", "docs/*.md"]
SOURCES_LIMIT = 40000

PIPELINE = (
    "Friday runs the plan as a pipeline: each milestone (2-5 stages) is implemented in one session by a cheaper executor "
    "model that can edit files and run the project's lint, typecheck and test commands but not browse the web. Each stage "
    "ends in one commit. An isolated gate (the project's validation command) runs at the end of each milestone, then a "
    "stronger model of another vendor reviews the milestone diff with the gate log. Milestones marked ui get a browser "
    "acceptance check on a local stand. At the end a final review and a blind check see the whole diff and the criteria "
    "only. The owner approves the plan once and next sees a draft merge request.")

READ_DOCS = (
    "Read the project's agent instructions (AGENTS.md, CLAUDE.md), its architecture index and the documents that apply "
    "to this task once, and put a digest of the rules the executor must follow into rules, each ending with its source "
    "path. Executors do not reread those documents.\n")
USE_DIGEST = (
    "The project's rules digest below was made from its agent instructions and documents. Put the rules that apply to "
    "this task into rules, each ending with its source path. Do not read those documents whole: when the digest is not "
    "enough, Grep the document the map names for the section you need. Executors do not reread those documents.\n")
PLAN_RULES = (
    "Write everything in English, whatever the language of the ticket: brief, steps, criteria, goal, title, summary, "
    "rules, assumptions and questions. Friday translates what the owner reads.\n"
    "{docs}"
    "Group stages into milestones that are reviewable as a whole. Order milestones with depends_on; set parallel only "
    "when the milestone shares no files with any milestone it is not ordered against.\n"
    "complexity is complex when the milestone needs design judgment across modules or the approach is uncertain; "
    "otherwise clear. ui is true when the milestone changes what a user sees or does.\n"
    "Each stage lists the existing files it changes in files and the files it creates in new_files; a milestone's "
    "files cover all files of its stages. Keep each stage within {stage_files} files and {stage_lines} added lines "
    "including tests; estimate_lines from the size of a comparable slice already in the repository.\n"
    "Every stage has criteria. A code criterion must be verifiable from that stage's own diff; a behavior criterion "
    "must be observable in the browser and is allowed only in ui milestones, and every ui milestone has at least one. "
    "No criterion may depend on work planned for a later stage.\n"
    "Resolve uncertainties from the repository and the ticket, record each choice with its reason in assumptions, and "
    "put in questions only what blocks a correct result; the owner reads them at approval. Do not edit any file.\n"
    "{reading}When you need several files, read them all in one turn with parallel Read calls rather than one per turn.")
USE_MAP = "Use the repository map below instead of listing directories. "

REVIEW_RULES = (
    "Review the plan against the ticket, the owner's decisions and the repository. Report every problem in one pass. "
    "Look for: requirements of the ticket the plan misses or exceeds; criteria that depend on a later stage (for "
    "example a stage that must prove a route filter works while the route itself is wired in the next stage); criteria "
    "that cannot be verified from the stage diff or in the browser; unrealistic sizes; wrong or missing files; breaches "
    "of the project's rules; milestones marked parallel that can collide. The current text of the "
    "files the plan changes is below; read other files only to check a point, several in one turn with parallel Read "
    "calls. Use blocking for problems that would make "
    "the result wrong or the pipeline fail, minor otherwise. Do not report wording or style. Write problem and "
    "suggestion in English. Do not edit any file. verdict is ok only when there are no blocking findings.")


def validate(plan, worktree, limits=None):
    """Feasibility checks that need no model; returns a list of problems in plain words."""
    limits = dict(LIMITS, **(limits or {}))
    errors = []
    milestones = plan.get("milestones") or []
    if not milestones:
        return ["The plan has no milestones."]
    ids = [item["id"] for item in milestones]
    stage_ids = [stage["id"] for item in milestones for stage in item["stages"]]
    for name, count in Counter(ids + stage_ids).items():
        if count > 1:
            errors.append("Duplicate id %s." % name)
    order = {name: index for index, name in enumerate(ids)}
    after = {}
    for index, item in enumerate(milestones):
        for dependency in item["depends_on"]:
            if order.get(dependency, len(ids)) >= index:
                errors.append("Milestone %s depends on %s, which is not an earlier milestone." % (item["id"], dependency))
        after[item["id"]] = set(item["depends_on"]).union(*(after.get(name, set()) for name in item["depends_on"]))
        if not item["stages"]:
            errors.append("Milestone %s has no stages." % item["id"])
        if item["ui"] and not any(criterion["check"] == "behavior" for stage in item["stages"] for criterion in stage["criteria"]):
            errors.append("Milestone %s changes the interface but has no behavior criterion." % item["id"])
        for stage in item["stages"]:
            where = "Stage %s" % stage["id"]
            touched = stage["files"] + stage["new_files"]
            outside = sorted(set(touched) - set(item["files"]))
            if outside:
                errors.append("%s uses files missing from milestone %s: %s." % (where, item["id"], ", ".join(outside)))
            if len(set(touched)) > limits["stage_files"]:
                errors.append("%s touches %d files; the limit is %d." % (where, len(set(touched)), limits["stage_files"]))
            if stage["estimate_lines"] > limits["stage_lines"]:
                errors.append("%s estimates %d lines; the limit is %d." % (where, stage["estimate_lines"], limits["stage_lines"]))
            for path in touched:
                if path.startswith("/") or ".." in Path(path).parts:
                    errors.append("%s has an unsafe path %s." % (where, path))
            missing = [path for path in stage["files"] if not (Path(worktree) / path).is_file()]
            if missing:
                errors.append("%s changes files that do not exist: %s." % (where, ", ".join(missing)))
            present = [path for path in stage["new_files"] if (Path(worktree) / path).exists()]
            if present:
                errors.append("%s creates files that already exist: %s." % (where, ", ".join(present)))
            if not stage["criteria"]:
                errors.append("%s has no criteria." % where)
            if not item["ui"] and any(criterion["check"] == "behavior" for criterion in stage["criteria"]):
                errors.append("%s has a behavior criterion in a milestone not marked ui." % where)
    for item in milestones:
        if not item["parallel"]:
            continue
        for other in milestones:
            if other is item or other["id"] in after[item["id"]] or item["id"] in after[other["id"]]:
                continue
            shared = sorted(set(item["files"]) & set(other["files"]))
            if shared:
                errors.append("Milestone %s is parallel but shares files with %s: %s." % (item["id"], other["id"], ", ".join(shared)))
    return errors


def plan_files(plan):
    return {path for item in plan.get("milestones", []) for path in item["files"]}


def overlaps(root, key, plan, profile):
    """Files this plan shares with other active tasks of the same repository: future merge conflicts."""
    warnings = []
    mine = plan_files(plan)
    for other in cards.keys(root):
        if other == key:
            continue
        value = cards.read(root, other)
        source = cards.directory(root, other) / "plan.json"
        if value["state"] not in ACTIVE or not source.is_file():
            continue
        try:
            same_repo = Path(profiles.load(value["profile"])["repository"]).resolve() == Path(profile["repository"]).resolve()
        except FridayError:
            same_repo = True  # an unreadable profile is no proof of another repository
        shared = sorted(mine & plan_files(json.loads(source.read_text(encoding="utf-8"))))
        if same_repo and shared:
            warnings.append("Task %s changes the same files: %s" % (other, ", ".join(shared[:10]) + (" …" if len(shared) > 10 else "")))
    return warnings


def ticket_text(ticket):
    if isinstance(ticket, str):
        return ticket
    parts = [ticket.get("key", ""), ticket.get("summary", ""), ticket.get("description", "")]
    comments = [comment.get("text", "") for comment in ticket.get("comments", []) if isinstance(comment, dict)]
    if comments:
        parts.append("Comments:\n" + "\n---\n".join(comments))
    return "\n\n".join(part for part in parts if part)


def context(card, profile, ticket):
    value = {"task": card["key"], "ticket": ticket_text(ticket)}
    if card["decisions"]:
        value["owner_decisions"] = cards.for_agents(card["decisions"])
    if profile.get("engineering_policy"):
        value["engineering_policy"] = profile["engineering_policy"]
    return json.dumps(value, ensure_ascii=False, indent=2)


def rules_sources(profile, worktree):
    """The tracked documents the rules digest is made from, in a stable order."""
    found = set()
    for pattern in profile.get("rules_docs", RULES_DOCS):
        found.update(path for path in Path(worktree).glob(pattern) if path.is_file())
    return sorted(str(path.relative_to(worktree)) for path in found)


def digest_text(digest):
    lines = ["Rules digest (from %s):" % ", ".join(digest["files"])] + ["- " + rule for rule in digest["rules"]]
    lines += ["", "Document map:"] + ["- %s: %s" % (item["path"], item["covers"]) for item in digest["documents"]]
    return "\n".join(lines)


def rules_digest(root, key, profile, worktree):
    """A digest of the project's rules, made once per content of its documents and shared by every task that has the same."""
    files = rules_sources(profile, worktree)
    if not files:
        return None
    fingerprint = hashlib.sha256()
    for name in files:
        fingerprint.update(name.encode("utf-8") + b"\0" + (Path(worktree) / name).read_bytes() + b"\0")
    path = Path(root) / "digests" / (fingerprint.hexdigest() + ".json")
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        cards.record(root, key, "digest_reused", files=len(files))
        return cached
    except (OSError, ValueError):
        pass
    prompt = ("Read these files of the repository whole: %s.\n"
              "Write a digest for those who plan and implement changes here and will not read the files. rules: every rule "
              "a change must follow (architecture and layers, naming, code style, tests, UI kit, what is forbidden), one "
              "short rule per item, each ending with its source path and section. documents: for each file, the topics it "
              "covers, so a planner can Grep the right section when a point is missing. Write in English, whatever the documents' language. "
              "Do not edit any file." % ", ".join(files))
    result = agent(root, key, profile, "digest", prompt, DIGEST_SCHEMA, cast_role="plan")
    digest = dict(result.structured, files=files)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(digest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cards.record(root, key, "digest_made", files=len(files), rules=len(digest["rules"]))
    return digest


def agent(root, key, profile, role, prompt, schema, cast_role=None):
    card = cards.read(root, key)
    entry = card["cast"][cast_role or role]
    mcp = repomap.server(root, profile, card["worktree"]) if role in ("plan", "plan-review") else None
    spec = runner.Spec(driver=entry["driver"], model=entry["model"], effort=entry.get("effort"), prompt=prompt,
                       cwd=card["worktree"], log_dir="", schema=schema, env=models.environment(entry, profile), mcp=mcp)
    result = cards.run_agent(root, key, spec, role=role, phase=role, entry=entry, profile=profile)
    if result.status != "ok" or not isinstance(result.structured, dict):
        raise FridayError("agent_failed", "{role} did not return a result ({status}): {value}", role=role, status=result.status, value=result.error or "no structured answer")
    return result


def revise(root, key, profile, prompt, document, instruction):
    """Revise a plan in a new session: the planning prompt, the plan and what to change, without the first session's reading."""
    text = (prompt + "\n\nYour plan:\n" + json.dumps(document, ensure_ascii=False, indent=2) + "\n\n"
            "This is a revision of the plan above, not a new plan: change what the points below require and keep the rest; "
            "read only the files you need to decide on each point.\n\n" + instruction)
    return agent(root, key, profile, "plan", text, PLAN_SCHEMA)


def checked(root, key, profile, worktree, result, limits, prompt, retries=1):
    """Return a plan that passes the feasibility checks, giving the planner its errors in a revision."""
    for attempt in range(retries + 1):
        errors = validate(result.structured, worktree, limits)
        if not errors:
            return result
        cards.record(root, key, "plan_invalid", errors=errors)
        if attempt == retries:
            raise FridayError("plan_invalid", "The plan fails feasibility checks:\n- {items}", items="\n- ".join(errors))
        result = revise(root, key, profile, prompt, result.structured,
                        "Friday's feasibility checks rejected the plan. Fix every problem and return the whole plan again "
                        "with the same schema; keep responses as they were.\n- " + "\n- ".join(errors))


def plan_sources(worktree, plan, limit=SOURCES_LIMIT):
    """The current text of the files the plan changes, for the reviewer; what does not fit is named, to be read."""
    parts, left, cut = [], limit, []
    for name in sorted(plan_files(plan)):
        path = Path(worktree) / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > left:
            cut.append(name)
            continue
        parts.append("--- %s\n%s" % (name, text))
        left -= len(text)
    if cut:
        parts.append("Not included, read if needed: " + ", ".join(cut))
    return "\n\n".join(parts)


def draft_path(root, key):
    return cards.directory(root, key) / "plan-draft.json"


def save_draft(root, key, prompt, planner, result):
    """The checked first plan survives a failed start, so a rerun with the same prompt does not plan again."""
    draft_path(root, key).write_text(json.dumps({"prompt": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "planner": planner,
                                                 "session": result.session, "plan": result.structured},
                                                ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def saved_draft(root, key, prompt):
    try:
        saved = json.loads(draft_path(root, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return saved if saved.get("prompt") == hashlib.sha256(prompt.encode("utf-8")).hexdigest() else None


def plan_review(root, key, profile, prompt):
    """Review the plan; a reviewer out of quota is replaced once by the next model the rules allow."""
    for attempt in range(2):
        try:
            return agent(root, key, profile, "plan-review", prompt, REVIEW_SCHEMA).structured
        except FridayError as exc:
            if attempt or "(quota)" not in str(exc):
                raise
        layout = cards.read(root, key)["cast"]
        entry, note = models.reviewer(profile, layout, "plan-review", layout["plan"], models.availability(root))
        if entry["model"] == layout["plan-review"]["model"]:
            raise FridayError("agent_failed", "No plan reviewer with quota is available.")
        cards.update(root, key, lambda value: value["cast"].update({"plan-review": entry}))
        cards.record(root, key, "cast_note", note=note or "plan-review: replaced after its quota ran out")


def make(root, key, profile, ticket):
    """Plan the task: cast, plan, feasibility checks, review by another model, one revision, ready for approval."""
    card = cards.read(root, key)
    limits = profile.get("limits")
    def rules(docs, reading=""):
        return PLAN_RULES.format(**dict(LIMITS, **(limits or {}), docs=docs, reading=reading))
    task = "\n\nTask:\n" + context(card, profile, ticket)
    identity = PIPELINE + "\n\n" + rules(READ_DOCS) + task  # the draft key does not depend on the digest or map caches
    saved = saved_draft(root, key, identity)
    layout, notes = models.cast(profile, models.availability(root), planner=saved and saved["planner"])
    cards.update(root, key, lambda value: value.update(cast=layout, state="planning"))
    for note in notes:
        cards.record(root, key, "cast_note", note=note)
    worktree = card["worktree"]
    digest = rules_digest(root, key, profile, worktree)
    repo_map = repomap.make(root, key, profile, worktree)
    project = ("\n\n" + digest_text(digest) if digest else "") + ("\n\n" + repo_map if repo_map else "")
    prompt = PIPELINE + "\n\n" + rules(USE_DIGEST if digest else READ_DOCS, USE_MAP if repo_map else "") + task + project
    if saved and saved["planner"] == layout["plan"]["name"]:
        planned = SimpleNamespace(structured=saved["plan"], session=saved["session"])
        cards.record(root, key, "plan_reused", reason="the checked plan of the failed start")
    else:
        planned = save_draft(root, key, identity, layout["plan"]["name"],
                             checked(root, key, profile, worktree, agent(root, key, profile, "plan", prompt, PLAN_SCHEMA), limits, prompt))
    review_prompt = (PIPELINE + "\n\n" + REVIEW_RULES + task + project +
                     "\n\nPlan:\n" + json.dumps(dict(planned.structured, responses=[]), ensure_ascii=False, indent=2) +
                     "\n\nFiles the plan changes:\n" + plan_sources(worktree, planned.structured))
    review = plan_review(root, key, profile, review_prompt)
    findings = review["findings"]
    cards.record(root, key, "plan_reviewed", verdict=review["verdict"], findings=len(findings),
                 blocking=sum(item["severity"] == "blocking" for item in findings))
    final = planned
    if findings:
        listing = "\n".join("%d. [%s] %s/%s: %s Suggestion: %s" % (index, item["severity"], item["milestone"], item["stage"],
                                                                  item["problem"], item["suggestion"])
                            for index, item in enumerate(findings))
        instruction = ("A reviewer of another vendor checked your plan. For each finding decide on the evidence: accept it "
                       "and change the plan, or reject it with a reason that cites the repository or the ticket. Return the "
                       "whole revised plan and one response per finding, using the finding numbers below.\n\n" + listing)
        final = checked(root, key, profile, worktree, revise(root, key, profile, prompt, planned.structured, instruction), limits, prompt)
    document = final.structured
    responses = {item["finding"]: item for item in document.get("responses", [])}
    open_notes = []
    for index, item in enumerate(findings):
        answer = responses.get(index)
        if answer is None or not answer["accepted"]:
            open_notes.append(dict(item, answer=(answer or {}).get("reason", "The planner did not answer this finding.")))
    warnings = overlaps(root, key, document, profile)
    (cards.directory(root, key) / "plan.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    skeleton = [{"id": item["id"], "title": item["title"], "complexity": item["complexity"], "ui": item["ui"],
                 "parallel": item["parallel"], "depends_on": item["depends_on"], "state": "pending",
                 "stages": [{"id": stage["id"], "commit": None} for stage in item["stages"]]} for item in document["milestones"]]
    def ready(value):
        value["plan"] = {"revision": value["plan"]["revision"] + 1, "approved": False, "review_notes": open_notes,
                         "overlaps": warnings, "questions": document["questions"]}
        value["milestones"] = skeleton
        value["state"] = "awaiting_approval"
    cards.update(root, key, ready)
    cards.record(root, key, "plan_ready", milestones=len(skeleton), open_notes=len(open_notes), overlaps=len(warnings))
    return document


def approve(root, key, by="owner"):
    """Approve the waiting plan; by="auto" when the profile's approval is auto and nobody looked at it."""
    def change(value):
        if value["state"] != "awaiting_approval":
            raise FridayError("invalid_state", "Task {key} has no plan waiting for approval.", key=key)
        value["plan"]["approved"] = True
        value["state"] = "approved"
    cards.update(root, key, change)
    cards.record(root, key, "plan_approved", by=by)


def render(root, key, profile=None):
    """The plan as the owner reads it, in their language; the plan itself stays in English."""
    from . import i18n, translate
    from .i18n import _
    value = cards.read(root, key)
    source = cards.directory(root, key) / "plan.json"
    if not source.is_file():
        raise FridayError("not_found", "Task {key} has no plan yet.", key=key)
    document = json.loads(source.read_text(encoding="utf-8"))
    if profile is None:
        try:
            profile = profiles.load(value["profile"])
        except (FridayError, OSError):
            profile = None
    lang = i18n.language()
    t = translate.into(root, key, profile or {}, lang)
    if profile is not None:
        texts = [document["summary"]] + value["plan"].get("questions", []) + value["plan"].get("overlaps", [])
        texts += [text for note in value["plan"].get("review_notes", []) for text in (note["problem"], note["answer"])]
        texts += document["assumptions"] + document["rules"]
        for item in document["milestones"]:
            texts += [item["title"], item["goal"]]
            for stage in item["stages"]:
                texts += [stage["brief"]] + [criterion["text"] for criterion in stage["criteria"]]
        t.prefetch(texts)
    lines = ["# " + _("Plan {key}", key=key), "", t(document["summary"]), ""]
    if value["plan"].get("questions"):
        lines += ["## " + _("Questions for the owner")] + ["- " + t(item) for item in value["plan"]["questions"]] + [""]
    if value["plan"].get("review_notes"):
        lines.append("## " + _("Open review notes"))
        for note in value["plan"]["review_notes"]:
            lines.append("- [%s] %s/%s: %s" % (note["severity"], note["milestone"], note["stage"], t(note["problem"])))
            lines.append("  " + _("Planner's answer: {answer}", answer=t(note["answer"])))
        lines.append("")
    if value["plan"].get("overlaps"):
        lines += ["## " + _("Overlaps with other tasks")] + ["- " + t(item) for item in value["plan"]["overlaps"]] + [""]
    lines.append("## " + _("Milestones"))
    for item in document["milestones"]:
        flags = ", ".join(flag for flag, on in ((_("complex"), item["complexity"] == "complex"), (_("interface"), item["ui"]),
                                                (_("parallel"), item["parallel"])) if on)
        after = " " + _("after {milestones}", milestones=", ".join(item["depends_on"])) if item["depends_on"] else ""
        lines.append("### %s. %s%s%s" % (item["id"], t(item["title"]), " (" + flags + ")" if flags else "", after))
        lines.append(t(item["goal"]))
        for stage in item["stages"]:
            lines.append("- **%s** %s (%s)" % (stage["id"], t(stage["brief"]), _("~{lines} lines, {files} files", lines=stage["estimate_lines"],
                                                                                   files=len(stage["files"]) + len(stage["new_files"]))))
            for criterion in stage["criteria"]:
                lines.append("  - [%s] %s" % (_("browser") if criterion["check"] == "behavior" else _("code"), t(criterion["text"])))
        lines.append("")
    if document["assumptions"]:
        lines += ["## " + _("Assumptions")] + ["- " + t(item) for item in document["assumptions"]] + [""]
    if document["rules"]:
        lines += ["## " + _("Project rules for the executor")] + ["- " + t(item) for item in document["rules"]] + [""]
    lines.append(_("Models: {cast}", cast=", ".join("%s → %s" % (role, entry["model"] + (" " + entry["effort"] if entry.get("effort") else ""))
                                                   for role, entry in value["cast"].items())))
    return "\n".join(lines)
