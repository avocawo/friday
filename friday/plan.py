"""Make a task plan: planner, deterministic feasibility checks, review by another model, one revision round."""
import difflib
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from . import card as cards
from . import design, models, repomap, runner, stories
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
# With Figma frames the plan also carries the design specification taken from them.
DESIGN_PLAN_SCHEMA = dict(PLAN_SCHEMA, required=PLAN_SCHEMA["required"] + ["design"],
                          properties=dict(PLAN_SCHEMA["properties"], design=STRINGS))
# A project's own planning process (ADR 0015) answers to its own review, so Friday asks for no responses.
PROJECT_PLAN_SCHEMA = dict(PLAN_SCHEMA, required=[name for name in PLAN_SCHEMA["required"] if name != "responses"],
                           properties={name: value for name, value in PLAN_SCHEMA["properties"].items() if name != "responses"})
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
    "enough, Grep the document the map names for the section you need. Executors and reviewers get the rules you choose "
    "and the document map, so end every rule with the document and section it comes from.\n")
PLAN_RULES = (
    "Write everything in English, whatever the language of the ticket: brief, steps, criteria, goal, title, summary, "
    "rules, assumptions and questions. Friday translates what the owner reads.\n"
    "{docs}"
    "Group stages into milestones that are reviewable as a whole. Order milestones with depends_on; set parallel only "
    "when the milestone shares no files with any milestone it is not ordered against.\n"
    "complexity is complex when the milestone needs design judgment across modules or the approach is uncertain; "
    "otherwise clear. ui is true when the milestone changes what a user sees or does.\n"
    "Each stage lists the existing files it changes in files and the files it creates in new_files; a milestone's "
    "files cover all files of its stages. A file created in an earlier stage of the same milestone or in a declared "
    "milestone dependency is existing at that stage; otherwise it must exist in the source snapshot. "
    "Keep each stage within {stage_files} files and {stage_lines} added lines "
    "including tests; estimate_lines from the size of a comparable slice already in the repository.\n"
    "Every stage has criteria. A code criterion must be verifiable from that stage's own diff; a behavior criterion "
    "must be observable in the browser and is allowed only in ui milestones, and every ui milestone has at least one. "
    "No criterion may depend on work planned for a later stage.\n"
    "Resolve uncertainties from the repository and the ticket, record each choice with its reason in assumptions, and "
    "put in questions only what blocks a correct result; the owner reads them at approval.\n"
    "When a design choice has more than one workable option (for example one operation that picks its call by target "
    "or one operation per target, one generic module or a wrapper per case), record in assumptions every option you "
    "considered with its estimated files and added lines. Choose the option with fewer new modules and lines unless a "
    "project rule you cite or a failure you can show rules it out; \"it would branch on a kind\", \"each instance would "
    "hold both dependencies\" or \"it is cleaner\" is not such a reason. This compares ways to build the same behavior; "
    "it is not a reason to merge unrelated work into one function. A constraint you set yourself rather than take "
    "from the ticket or a project rule, such as keeping a public API unchanged, is an assumption too: record it with "
    "the lines it costs, not as a given. Do not edit any file.\n"
    "{reading}When you need several files, read them all in one turn with parallel Read calls rather than one per turn.")
USE_MAP = "Use the repository map below instead of listing directories. "

PROJECT_PLAN_RULES = (
    "This session runs inside Friday with no human at the keyboard. Where your process would show the plan and wait for "
    "approval or an answer, write the plan to {plan_file} and end the session instead; the owner reads that file and "
    "approves it in Friday. Do not commit.\n"
    "The structured answer is Friday's summary of the same plan, in English. Group the plan's stages into "
    "milestones that are reviewable as a whole and order them with depends_on; set parallel only when a milestone shares "
    "no files with any milestone it is not ordered against. ui is true when the milestone changes what a user sees or does. "
    "Each stage lists the existing files it changes in files, the files it creates in new_files, its added lines in "
    "estimate_lines and its criteria: a code criterion is verifiable from the stage's own diff, a behavior criterion is "
    "observable in the browser and belongs only to ui milestones, and every ui milestone has one. rules are the project "
    "rules the plan relies on, each with its source. For each design choice with more than one workable option, "
    "assumptions list every option considered with its estimated files and added lines; choose the one with fewer new "
    "modules and lines unless a cited project rule or a shown failure rules it out, since taste (\"it would branch\", "
    "\"it is cleaner\") is no reason. A constraint you set yourself, such as keeping a public API unchanged, is an "
    "assumption with the lines it costs. Put in questions what the owner must answer before approval.")

REVIEW_RULES = (
    "Review the plan against the ticket, the owner's decisions and the repository. Report every problem in one pass. "
    "Look for: requirements of the ticket the plan misses or exceeds; criteria that depend on a later stage (for "
    "example a stage that must prove a route filter works while the route itself is wired in the next stage); criteria "
    "that cannot be verified from the stage diff or in the browser; unrealistic sizes; wrong or missing files; breaches "
    "of the project's rules; milestones marked parallel that can collide; an option rejected in assumptions without a "
    "cited project rule or a shown failure, a constraint the planner set itself without counting its cost, and a "
    "simpler design with fewer new modules or lines that the plan did not consider. The current text of the "
    "files the plan changes is below; read other files only to check a point, several in one turn with parallel Read "
    "calls. Use blocking for problems that would make "
    "the result wrong or the pipeline fail, minor otherwise. The three design findings above are minor, and blocking "
    "when the plan's design is larger than the alternative by more than about a third in files or added lines; "
    "name both sizes in the finding. "
    "Do not report wording or style. Write problem and "
    "suggestion in English. Do not edit any file. verdict is ok only when there are no blocking findings.")
REVIEW_CHANGED = (
    "\nThis plan was reviewed before. The owner's latest decision changed milestones %s: review only them and what "
    "their change breaks in the rest of the plan. The other milestones are context, carried over as they were reviewed. "
    "The files below are those of the changed milestones.")
AMEND = (
    "The owner decided before approving the plan:\n%s\n\nRevise the plan by this decision. %s Rewrite the steps, files "
    "and criteria the decision contradicts, so that no stage asks for what the owner ruled out. Keep in questions only "
    "what is still open; return the whole plan with an empty responses list.")
AMEND_NAMED = "Change only milestones %s; every other milestone stays exactly as it is."
AMEND_FREE = "Change only the milestones the decision affects; every other milestone stays exactly as it is."


def locate(worktree, name, showcase=None):
    """A plan path as a file: stories:… names a file of the showcase repository, anything else one of the task's."""
    if name.startswith(stories.PREFIX):
        return Path(showcase) / name[len(stories.PREFIX):] if showcase else None
    return Path(worktree) / name


def validate(plan, worktree, limits=None, showcase=None):
    """Feasibility checks that need no model; returns a list of problems in plain words."""
    limits = dict(LIMITS, **(limits or {}))
    errors = []
    milestones = plan.get("milestones") or []
    if not milestones:
        return ["The plan has no milestones."]
    ids = [item["id"] for item in milestones]
    stage_ids = [stage["id"] for item in milestones for stage in item["stages"]]
    for field, count, label in (("task_milestones", len(milestones), "milestones"),
                                ("task_stages", len(stage_ids), "stages")):
        if field in limits and count > limits[field]:
            errors.append("The plan has %d %s; the task limit is %d. Split the task before implementation."
                          % (count, label, limits[field]))
    for name, count in Counter(ids + stage_ids).items():
        if count > 1:
            errors.append("Duplicate id %s." % name)
    order = {name: index for index, name in enumerate(ids)}
    after, created, creators = {}, {}, set()
    for index, item in enumerate(milestones):
        for dependency in item["depends_on"]:
            if order.get(dependency, len(ids)) >= index:
                errors.append("Milestone %s depends on %s, which is not an earlier milestone." % (item["id"], dependency))
        after[item["id"]] = set(item["depends_on"]).union(*(after.get(name, set()) for name in item["depends_on"]))
        available = set().union(*(created.get(name, set()) for name in after[item["id"]]))
        created[item["id"]] = set()
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
                bare = path[len(stories.PREFIX):] if path.startswith(stories.PREFIX) else path
                if bare.startswith("/") or ".." in Path(bare).parts:
                    errors.append("%s has an unsafe path %s." % (where, path))
                elif locate(worktree, path, showcase) is None:
                    errors.append("%s names %s, but the task has no showcase repository." % (where, path))
            missing = [path for path in stage["files"]
                       if locate(worktree, path, showcase) not in available
                       and not (locate(worktree, path, showcase) or Path("/nonexistent")).is_file()]
            if missing:
                errors.append("%s changes files that do not exist: %s." % (where, ", ".join(missing)))
            present = [path for path in stage["new_files"]
                       if locate(worktree, path, showcase) in creators
                       or (locate(worktree, path, showcase) or Path("/nonexistent")).exists()]
            if present:
                errors.append("%s creates files that already exist: %s." % (where, ", ".join(present)))
            new_paths = {locate(worktree, path, showcase) for path in stage["new_files"]} - {None}
            created[item["id"]].update(new_paths)
            available.update(new_paths)
            creators.update(new_paths)
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
    if design.frames(card):
        value["design_frames"] = design.frames(card)
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
    mcp = None
    if role in ("plan", "plan-review"):
        servers = [repomap.server(root, profile, card["worktree"])]
        if design.frames(card):
            servers.append(design.figma_server(profile))
        mcp = [server for server in servers if server] or None
    spec = runner.Spec(driver=entry["driver"], model=entry["model"], effort=entry.get("effort"), prompt=prompt,
                       cwd=card["worktree"], log_dir="", schema=schema, env=models.environment(entry, profile), mcp=mcp,
                       add_dirs=[stories.worktree(card)] if stories.worktree(card) else [])
    result = cards.run_agent(root, key, spec, role=role, phase=role, entry=entry, profile=profile)
    if result.status != "ok" or not isinstance(result.structured, dict):
        raise FridayError("agent_failed", "{role} did not return a result ({status}): {value}", role=role, status=result.status, value=result.error or "no structured answer")
    return result


def revise(root, key, profile, prompt, document, instruction):
    """Revise a plan in a new session: the planning prompt, the plan and what to change, without the first session's reading."""
    text = (prompt + "\n\nYour plan:\n" + json.dumps(document, ensure_ascii=False, indent=2) + "\n\n"
            "This is a revision of the plan above, not a new plan: change what the points below require and keep the rest; "
            "read only the files you need to decide on each point.\n\n" + instruction)
    return agent(root, key, profile, "plan", text, plan_schema(root, key))


def plan_schema(root, key):
    return DESIGN_PLAN_SCHEMA if design.frames(cards.read(root, key)) else PLAN_SCHEMA


def carry(document, kept):
    """Put back the milestones a revision had to keep; one the planner dropped is an error for it to fix."""
    ids = {item["id"] for item in document["milestones"]}
    document["milestones"] = [kept.get(item["id"], item) for item in document["milestones"]]
    return ["Milestone %s is outside the owner's decision and must stay in the plan as it was." % name
            for name in kept if name not in ids]


def changes(before, after):
    """Milestones a revision changed or added, and those it removed."""
    old = {item["id"]: item for item in before["milestones"]}
    new = [item["id"] for item in after["milestones"]]
    return [item["id"] for item in after["milestones"] if old.get(item["id"]) != item], [name for name in old if name not in new]


def checked(root, key, profile, worktree, result, limits, prompt, retries=1, kept=None):
    """Return a plan that passes the feasibility checks, giving the planner its errors in a revision."""
    for attempt in range(retries + 1):
        errors = carry(result.structured, kept) if kept else []
        errors += validate(result.structured, worktree, limits, stories.worktree(cards.read(root, key)))
        if design.frames(cards.read(root, key)) and not result.structured.get("design"):
            errors.append("The ticket links Figma frames but the plan has no design specification.")
        if not errors:
            return result
        cards.record(root, key, "plan_invalid", errors=errors)
        if attempt == retries:
            raise FridayError("plan_invalid", "The plan fails feasibility checks:\n- {items}", items="\n- ".join(errors))
        result = revise(root, key, profile, prompt, result.structured,
                        "Friday's feasibility checks rejected the plan. Fix every problem and return the whole plan again "
                        "with the same schema; keep responses as they were.\n- " + "\n- ".join(errors))


def plan_sources(worktree, plan, limit=SOURCES_LIMIT, showcase=None):
    """The current text of the files the plan changes, for the reviewer; what does not fit is named, to be read."""
    parts, left, cut = [], limit, []
    for name in sorted(plan_files(plan)):
        path = locate(worktree, name, showcase)
        if path is None or not path.is_file():
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


def planning_rules(profile, card, docs, reading=""):
    limits = profile.get("limits")
    return PLAN_RULES.format(**dict(LIMITS, **(limits or {}), docs=docs, reading=reading)) + (
        "\n" + design.PLAN_FIGMA if design.frames(card) else "") + (
        stories.plan_text(profile) if stories.worktree(card) else "") + (
        "\n\nWhole-task limits: " + json.dumps({field: limits[field] for field in ("task_milestones", "task_stages")
                                             if field in limits}) + ". Split larger scope before implementation."
        if limits and any(field in limits for field in ("task_milestones", "task_stages")) else "")


def planning_identity(card, profile, ticket):
    return PIPELINE + "\n\n" + planning_rules(profile, card, READ_DOCS) + "\n\nTask:\n" + context(card, profile, ticket)


def restore_statement(root, key, profile, ticket):
    """Recover a legacy card's original brief, never substitute a new approved scope."""
    card = cards.read(root, key)
    text = ticket_text(ticket)
    if card["state"] not in ("failed", "stopped", "approved") or not card["plan"]["approved"]:
        raise FridayError("invalid_state", "Restore a brief only on an approved inactive task.")
    saved = card.get("task_statement")
    if saved == text:
        return
    if saved is not None or not saved_draft(root, key, planning_identity(card, profile, ticket)):
        raise FridayError("invalid_state", "The brief does not match the original planning input; replan changed requirements.")
    cards.update(root, key, lambda value: value.update(task_statement=text))
    cards.record(root, key, "task_statement_restored", sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())


def make(root, key, profile, ticket, use_design=True):
    """Plan the task: cast, plan, feasibility checks, review by another model, one revision, ready for approval."""
    if profiles.toolset(profile, "plan") == "project":
        return project_make(root, key, profile, ticket)
    design.prepare(root, key, profile, ticket_text(ticket), enabled=use_design)
    card = cards.update(root, key, lambda value: value.update(task_statement=ticket_text(ticket)))
    limits = profile.get("limits")
    identity = planning_identity(card, profile, ticket)  # independent of digest and map caches
    saved = saved_draft(root, key, identity)
    layout, notes = models.cast(profile, models.availability(root), planner=saved and saved["planner"])
    cards.update(root, key, lambda value: value.update(cast=layout, state="planning"))
    for note in notes:
        cards.record(root, key, "cast_note", note=note)
    worktree = card["worktree"]
    prompt, shared = prompts(root, key, profile, card, ticket)
    if saved and saved["planner"] == layout["plan"]["name"]:
        planned = SimpleNamespace(structured=saved["plan"], session=saved["session"])
        cards.record(root, key, "plan_reused", reason="the checked plan of the failed start")
    else:
        planned = save_draft(root, key, identity, layout["plan"]["name"],
                             checked(root, key, profile, worktree, agent(root, key, profile, "plan", prompt, plan_schema(root, key)),
                                     limits, prompt))
    document, open_notes = settle(root, key, profile, card, prompt, shared, planned)
    return ready_for_approval(root, key, profile, document, open_notes)


def prompts(root, key, profile, card, ticket):
    """The planner's prompt and the part the reviewer shares with it: the task, the rules digest and the repository map."""
    worktree = card["worktree"]
    digest = rules_digest(root, key, profile, worktree)
    cards.update(root, key, lambda value: value.update(doc_map=digest["documents"] if digest else []))
    repo_map = repomap.make(root, key, profile, worktree)
    shared = ("\n\nTask:\n" + context(card, profile, ticket) + ("\n\n" + digest_text(digest) if digest else "") +
              ("\n\n" + repo_map if repo_map else ""))
    return PIPELINE + "\n\n" + planning_rules(profile, card, USE_DIGEST if digest else READ_DOCS, USE_MAP if repo_map else "") + shared, shared


def settle(root, key, profile, card, prompt, shared, planned, only=None, kept=None):
    """Review by another model and one revision round; returns the plan and the findings its planner did not accept.
    With only, those milestones are under review and the rest of the plan is context (ADR 0016)."""
    worktree, plan = card["worktree"], planned.structured
    scope = plan if only is None else {"milestones": [item for item in plan["milestones"] if item["id"] in only]}
    review_prompt = (PIPELINE + "\n\n" + REVIEW_RULES + ("\n" + design.REVIEW_FIGMA if design.frames(card) else "") +
                     (REVIEW_CHANGED % ", ".join(only) if only is not None else "") + shared +
                     "\n\nPlan:\n" + json.dumps(dict(plan, responses=[]), ensure_ascii=False, indent=2) +
                     "\n\nFiles the plan changes:\n" + plan_sources(worktree, scope, showcase=stories.worktree(card)))
    review = plan_review(root, key, profile, review_prompt)
    findings = review["findings"]
    cards.record(root, key, "plan_reviewed", verdict=review["verdict"], findings=len(findings),
                 blocking=sum(item["severity"] == "blocking" for item in findings), **({"milestones": only} if only is not None else {}))
    final = planned
    if findings:
        listing = "\n".join("%d. [%s] %s/%s: %s Suggestion: %s" % (index, item["severity"], item["milestone"], item["stage"],
                                                                  item["problem"], item["suggestion"])
                            for index, item in enumerate(findings))
        instruction = ("A reviewer of another vendor checked your plan. For each finding decide on the evidence: accept it "
                       "and change the plan, or reject it with a reason that cites the repository or the ticket. Return the "
                       "whole revised plan and one response per finding, using the finding numbers below.\n\n" + listing)
        final = checked(root, key, profile, worktree, revise(root, key, profile, prompt, plan, instruction),
                        profile.get("limits"), prompt, kept=kept)
    document = final.structured
    responses = {item["finding"]: item for item in document.get("responses", [])}
    open_notes = []
    for index, item in enumerate(findings):
        answer = responses.get(index)
        if answer is None or not answer["accepted"]:
            open_notes.append(dict(item, answer=(answer or {}).get("reason", "The planner did not answer this finding.")))
    return document, open_notes


def amend(root, key, profile, text, reopen=()):
    """The owner decided before approval: the planner revises the plan by the decision in a new session, and only the
    milestones it changed are reviewed again (ADR 0016). Returns the milestones changed and those removed."""
    value = cards.read(root, key)
    if value["state"] != "awaiting_approval" or profiles.toolset(profile, "plan") == "project":
        raise FridayError("invalid_state", "Task {key} has no plan of Friday's planner waiting for approval.", key=key)
    before = json.loads((cards.directory(root, key) / "plan.json").read_text(encoding="utf-8"))
    kept = {item["id"]: item for item in before["milestones"] if item["id"] not in reopen} if reopen else None
    cards.update(root, key, lambda card: card.update(state="planning"))
    try:
        prompt, shared = prompts(root, key, profile, value, value.get("task_statement") or "")
        instruction = AMEND % (text, AMEND_NAMED % ", ".join(reopen) if reopen else AMEND_FREE)
        result = checked(root, key, profile, value["worktree"], revise(root, key, profile, prompt, before, instruction),
                         profile.get("limits"), prompt, kept=kept)
        document, notes = result.structured, []
        changed, removed = changes(before, document)
        if changed:
            document, notes = settle(root, key, profile, value, prompt, shared, result, only=changed, kept=kept)
            changed, removed = changes(before, document)
    except BaseException:
        cards.update(root, key, lambda card: card.update(state="awaiting_approval"))
        raise
    cards.record(root, key, "plan_amended", changed=changed, removed=removed)
    if dict(document, responses=[]) == dict(before, responses=[]):
        cards.update(root, key, lambda card: card.update(state="awaiting_approval"))  # the decision changes no part of the plan
        return changed, removed
    # Notes on the milestones the decision left alone still stand.
    standing = [note for note in value["plan"]["review_notes"]
                if note["milestone"] not in changed and note["milestone"] not in removed]
    ready_for_approval(root, key, profile, document, standing + notes, value["plan"].get("file"))
    return changed, removed


def ready_for_approval(root, key, profile, document, open_notes, plan_file=None):
    """Save the checked plan, lay out its milestones on the card and wait for the owner."""
    warnings = overlaps(root, key, document, profile)
    target = cards.directory(root, key) / "plan.json"
    if target.is_file():  # the revision before this one stays for friday plan --diff
        (cards.directory(root, key) / "plan-previous.json").write_bytes(target.read_bytes())
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    skeleton = [{"id": item["id"], "title": item["title"], "complexity": item["complexity"], "ui": item["ui"],
                 "parallel": item["parallel"], "depends_on": item["depends_on"], "state": "pending",
                 "stages": [{"id": stage["id"], "commit": None} for stage in item["stages"]]} for item in document["milestones"]]
    def ready(value):
        value["plan"] = {"revision": value["plan"]["revision"] + 1, "approved": False, "review_notes": open_notes,
                         "overlaps": warnings, "questions": document["questions"]}
        if plan_file:
            value["plan"]["file"] = plan_file
        value["milestones"] = skeleton
        value["state"] = "awaiting_approval"
    cards.update(root, key, ready)
    cards.record(root, key, "plan_ready", milestones=len(skeleton), open_notes=len(open_notes), overlaps=len(warnings))
    return document


def project_session(root, key, profile, prompt, resume=None, role="plan", entry=None, schema=PROJECT_PLAN_SCHEMA,
                    session_field="plan_session"):
    """One session of the project's own process in the task's worktree (ADR 0015): its plan or its execution."""
    card = cards.read(root, key)
    entry = entry or card["cast"]["plan"]
    if entry["driver"] != "claude":
        raise FridayError("invalid_profile", "The project toolset runs Claude Code; the %s model is %s." % (role, entry["model"]))
    settings = profile["project"]
    env = dict(models.environment(entry, profile))
    env.update({name: os.environ[name] for name in settings.get("session_env", []) if name in os.environ})
    timeouts = {profiles.TIMEOUTS[name]: value for name, value in settings.get("timeouts", {}).items()}
    spec = runner.Spec(driver="claude", model=entry["model"], effort=entry.get("effort"), prompt=prompt, cwd=card["worktree"],
                       log_dir="", schema=schema, env=env, resume=resume, toolset="project",
                       disallowed_tools=settings.get("disallowed_tools", []), **timeouts)
    result = cards.run_agent(root, key, spec, role=role, phase=role, entry=entry, profile=profile)
    if result.status != "ok" or not isinstance(result.structured, dict):
        raise FridayError("agent_failed", "%s did not return a result (%s): %s" % (role, result.status, result.error or "no structured answer"))
    cards.update(root, key, lambda value: value.update({session_field: result.session}))
    return result


def project_checked(root, key, profile, result, retries=1):
    """The feasibility checks of Friday's own plans; the session gets its errors and continues once."""
    task = cards.read(root, key)
    for attempt in range(retries + 1):
        errors = validate(result.structured, task["worktree"], profile.get("limits"), stories.worktree(task))
        if not errors:
            return result.structured
        cards.record(root, key, "plan_invalid", errors=errors)
        if attempt == retries:
            raise FridayError("plan_invalid", "The plan fails feasibility checks:\n- " + "\n- ".join(errors))
        result = project_session(root, key, profile, "Friday's feasibility checks rejected the summary of your plan. Fix "
                                 "every problem in the plan file and in the answer, and return the whole summary again:\n- "
                                 + "\n- ".join(errors), resume=result.session)


def project_make(root, key, profile, ticket):
    """Plan with the project's own planning command; Friday checks the summary and lays it out for approval."""
    statement = ticket_text(ticket)
    layout, notes = models.cast(profile, models.availability(root))
    cards.update(root, key, lambda value: value.update(task_statement=statement, cast=layout, state="planning"))
    for note in notes:
        cards.record(root, key, "cast_note", note=note)
    settings = profile["project"]
    prompt = (settings["plan_command"].replace("{statement}", statement) + "\n\n" +
              PROJECT_PLAN_RULES.replace("{plan_file}", settings["plan_file"]))
    document = project_checked(root, key, profile, project_session(root, key, profile, prompt))
    plan_file = str(Path(cards.read(root, key)["worktree"]) / settings["plan_file"])
    return ready_for_approval(root, key, profile, document, [], plan_file)


def answer(root, key, profile, text):
    """The owner answered the plan's questions: the planning session continues with the answer (ADR 0015)."""
    value = cards.read(root, key)
    if value["state"] != "awaiting_approval" or profiles.toolset(profile, "plan") != "project" or not value.get("plan_session"):
        raise FridayError("invalid_state", "Task %s has no project plan waiting for an answer." % key)
    cards.update(root, key, lambda card: card.update(state="planning"))
    try:
        result = project_session(root, key, profile, "The owner answered:\n" + text + "\n\nRevise the plan file by this "
                                 "answer and return the whole summary again; keep in questions only what is still open.",
                                 resume=value["plan_session"])
        document = project_checked(root, key, profile, result)
    except BaseException:
        cards.update(root, key, lambda card: card.update(state="awaiting_approval"))
        raise
    return ready_for_approval(root, key, profile, document, [], value["plan"].get("file"))


def approve(root, key, by="owner"):
    """Approve the waiting plan; by="auto" when the profile's approval is auto and nobody looked at it."""
    def change(value):
        if value["state"] != "awaiting_approval":
            raise FridayError("invalid_state", "Task {key} has no plan waiting for approval.", key=key)
        value["plan"]["approved"] = True
        value["state"] = "approved"
    cards.update(root, key, change)
    cards.record(root, key, "plan_approved", by=by)


def heading(item, t=lambda text: text):
    from .i18n import _
    flags = ", ".join(flag for flag, on in ((_("complex"), item["complexity"] == "complex"), (_("interface"), item["ui"]),
                                            (_("parallel"), item["parallel"])) if on)
    after = " " + _("after {milestones}", milestones=", ".join(item["depends_on"])) if item["depends_on"] else ""
    return "### %s. %s%s%s" % (item["id"], t(item["title"]), " (" + flags + ")" if flags else "", after)


def outline(document):
    """The whole plan as lines, with the steps and files the executor gets: what friday plan --diff compares."""
    from .i18n import _
    lines = [document["summary"], ""]
    for title, field in ((_("Questions for the owner"), "questions"), (_("Mockup"), "design"), (_("Assumptions"), "assumptions"),
                         (_("Project rules for the executor"), "rules")):
        if document.get(field):
            lines += ["## " + title] + ["- " + item for item in document[field]] + [""]
    for item in document["milestones"]:
        lines += [heading(item), item["goal"], _("Files: {files}", files=", ".join(item["files"]))]
        for stage in item["stages"]:
            lines.append("- **%s** %s (%s)" % (stage["id"], stage["brief"], _("~{count} lines", count=stage["estimate_lines"])))
            lines += ["  " + _("step: {text}", text=step) for step in stage["steps"]]
            lines += ["  " + _("file: {name}", name=name) for name in stage["files"]]
            lines += ["  " + _("new file: {name}", name=name) for name in stage["new_files"]]
            lines += ["  - [%s] %s" % (_("browser") if criterion["check"] == "behavior" else _("code"), criterion["text"])
                      for criterion in stage["criteria"]]
        lines.append("")
    return lines


def diff(root, key):
    """What the last revision of the plan changed against the one before it."""
    from .i18n import _
    directory = cards.directory(root, key)
    try:
        before = json.loads((directory / "plan-previous.json").read_text(encoding="utf-8"))
        after = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    except OSError:
        raise FridayError("not_found", "Task {key} has no previous revision of the plan.", key=key)
    revision = cards.read(root, key)["plan"]["revision"]
    lines = list(difflib.unified_diff(outline(before), outline(after), _("revision {number}", number=revision - 1),
                                      _("revision {number}", number=revision), lineterm="", n=2))
    if not lines:
        return _("Revisions {before} and {after} of plan {key} are the same.", before=revision - 1, after=revision, key=key)
    changed, removed = changes(before, after)
    head = _("Changed milestones: {changed}", changed=", ".join(changed) or _("none"))
    if removed:
        head += "; " + _("removed: {removed}", removed=", ".join(removed))
    return "\n".join([head, ""] + lines)


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
    if value["plan"].get("file"):
        lines += [_("Project plan file: {path}", path=value["plan"]["file"]), ""]
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
        lines.append(heading(item, t))
        lines.append(t(item["goal"]))
        for stage in item["stages"]:
            lines.append("- **%s** %s (%s)" % (stage["id"], t(stage["brief"]), _("~{lines} lines, {files} files", lines=stage["estimate_lines"],
                                                                                   files=len(stage["files"]) + len(stage["new_files"]))))
            for criterion in stage["criteria"]:
                lines.append("  - [%s] %s" % (_("browser") if criterion["check"] == "behavior" else _("code"), t(criterion["text"])))
        lines.append("")
    if document.get("design"):
        lines += ["## " + _("Mockup")] + ["- " + item for item in document["design"]] + [""]
    if document["assumptions"]:
        lines += ["## " + _("Assumptions")] + ["- " + t(item) for item in document["assumptions"]] + [""]
    if document["rules"]:
        lines += ["## " + _("Project rules for the executor")] + ["- " + t(item) for item in document["rules"]] + [""]
    lines.append(_("Models: {cast}", cast=", ".join("%s → %s" % (role, entry["model"] + (" " + entry["effort"] if entry.get("effort") else ""))
                                                   for role, entry in value["cast"].items())))
    return "\n".join(lines)
