"""Make a task plan: planner, deterministic feasibility checks, review by another model, one revision round."""
import json
from collections import Counter
from pathlib import Path

from . import card as cards
from . import models, runner
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

PIPELINE = (
    "Friday runs the plan as a pipeline: each milestone (2-5 stages) is implemented in one session by a cheaper executor "
    "model that can edit files and run the project's lint, typecheck and test commands but not browse the web. Each stage "
    "ends in one commit. An isolated gate (the project's validation command) runs at the end of each milestone, then a "
    "stronger model of another vendor reviews the milestone diff with the gate log. Milestones marked ui get a browser "
    "acceptance check on a local stand. At the end a final review and a blind check see the whole diff and the criteria "
    "only. The owner approves the plan once and next sees a draft merge request.")

PLAN_RULES = (
    "Write brief, steps, criteria, goal, title, summary, rules, assumptions and questions in the task's language; in "
    "Russian call a milestone «веха» and a stage «этап».\n"
    "Read the project's agent instructions (AGENTS.md, CLAUDE.md), its architecture index and the documents that apply "
    "to this task once, and put a digest of the rules the executor must follow into rules, each ending with its source "
    "path. Executors do not reread those documents.\n"
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
    "put in questions only what blocks a correct result; the owner reads them at approval. Do not edit any file.")

REVIEW_RULES = (
    "Review the plan against the ticket, the owner's decisions and the repository. Report every problem in one pass. "
    "Look for: requirements of the ticket the plan misses or exceeds; criteria that depend on a later stage (for "
    "example a stage that must prove a route filter works while the route itself is wired in the next stage); criteria "
    "that cannot be verified from the stage diff or in the browser; unrealistic sizes; wrong or missing files; breaches "
    "of the project's rules; milestones marked parallel that can collide. Use blocking for problems that would make "
    "the result wrong or the pipeline fail, minor otherwise. Do not report wording or style. Write problem and "
    "suggestion in the task's language. Do not edit any file. verdict is ok only when there are no blocking findings.")


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
            warnings.append("Задача %s меняет те же файлы: %s" % (other, ", ".join(shared[:10]) + (" …" if len(shared) > 10 else "")))
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
        value["owner_decisions"] = card["decisions"]
    if profile.get("engineering_policy"):
        value["engineering_policy"] = profile["engineering_policy"]
    return json.dumps(value, ensure_ascii=False, indent=2)


def agent(root, key, profile, role, prompt, schema, resume=None):
    card = cards.read(root, key)
    entry = card["cast"][role]
    spec = runner.Spec(driver=entry["driver"], model=entry["model"], effort=entry.get("effort"), prompt=prompt,
                       cwd=card["worktree"], log_dir="", schema=schema, resume=resume, env=models.environment(entry, profile))
    result = cards.run_agent(root, key, spec, role=role, phase=role, entry=entry, profile=profile)
    if result.status != "ok" or not isinstance(result.structured, dict):
        raise FridayError("agent_failed", "%s did not return a result (%s): %s" % (role, result.status, result.error or "no structured answer"))
    return result


def checked(root, key, profile, worktree, result, limits, retries=1):
    """Return a plan that passes the feasibility checks, giving the planner its errors in the same session."""
    for attempt in range(retries + 1):
        errors = validate(result.structured, worktree, limits)
        if not errors:
            return result
        cards.record(root, key, "plan_invalid", errors=errors)
        if attempt == retries:
            raise FridayError("plan_invalid", "The plan fails feasibility checks:\n- " + "\n- ".join(errors))
        prompt = ("Friday's feasibility checks rejected the plan. Fix every problem and return the whole plan again "
                  "with the same schema; keep responses as they were.\n- " + "\n- ".join(errors))
        result = agent(root, key, profile, "plan", prompt, PLAN_SCHEMA, resume=result.session)


def make(root, key, profile, ticket):
    """Plan the task: cast, plan, feasibility checks, review by another model, one revision, ready for approval."""
    card = cards.read(root, key)
    limits = profile.get("limits")
    layout, notes = models.cast(profile, models.availability(root))
    cards.update(root, key, lambda value: value.update(cast=layout, state="planning"))
    for note in notes:
        cards.record(root, key, "cast_note", note=note)
    worktree = card["worktree"]
    rules = PLAN_RULES.format(**dict(LIMITS, **(limits or {})))
    prompt = PIPELINE + "\n\n" + rules + "\n\nTask:\n" + context(card, profile, ticket)
    planned = checked(root, key, profile, worktree, agent(root, key, profile, "plan", prompt, PLAN_SCHEMA), limits)
    review_prompt = (PIPELINE + "\n\n" + REVIEW_RULES + "\n\nTask:\n" + context(card, profile, ticket) +
                     "\n\nPlan:\n" + json.dumps(dict(planned.structured, responses=[]), ensure_ascii=False, indent=2))
    review = agent(root, key, profile, "plan-review", review_prompt, REVIEW_SCHEMA).structured
    findings = review["findings"]
    cards.record(root, key, "plan_reviewed", verdict=review["verdict"], findings=len(findings),
                 blocking=sum(item["severity"] == "blocking" for item in findings))
    final = planned
    if findings:
        listing = "\n".join("%d. [%s] %s/%s: %s Suggestion: %s" % (index, item["severity"], item["milestone"], item["stage"],
                                                                  item["problem"], item["suggestion"])
                            for index, item in enumerate(findings))
        prompt = ("A reviewer of another vendor checked your plan. For each finding decide on the evidence: accept it and "
                  "change the plan, or reject it with a reason that cites the repository or the ticket. Return the whole "
                  "revised plan and one response per finding, using the finding numbers below.\n\n" + listing)
        final = checked(root, key, profile, worktree, agent(root, key, profile, "plan", prompt, PLAN_SCHEMA, resume=planned.session), limits)
    document = final.structured
    responses = {item["finding"]: item for item in document.get("responses", [])}
    open_notes = []
    for index, item in enumerate(findings):
        answer = responses.get(index)
        if answer is None or not answer["accepted"]:
            open_notes.append(dict(item, answer=(answer or {}).get("reason", "Планировщик не ответил на замечание.")))
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


def approve(root, key):
    def change(value):
        if value["state"] != "awaiting_approval":
            raise FridayError("invalid_state", "Task %s has no plan waiting for approval." % key)
        value["plan"]["approved"] = True
        value["state"] = "approved"
    cards.update(root, key, change)
    cards.record(root, key, "plan_approved")


def render(root, key):
    value = cards.read(root, key)
    source = cards.directory(root, key) / "plan.json"
    if not source.is_file():
        raise FridayError("not_found", "Task %s has no plan yet." % key)
    document = json.loads(source.read_text(encoding="utf-8"))
    lines = ["# План %s" % key, "", document["summary"], ""]
    if value["plan"].get("questions"):
        lines += ["## Вопросы владельцу"] + ["- " + item for item in value["plan"]["questions"]] + [""]
    if value["plan"].get("review_notes"):
        lines.append("## Несогласованные замечания проверки")
        for note in value["plan"]["review_notes"]:
            lines.append("- [%s] %s/%s: %s" % (note["severity"], note["milestone"], note["stage"], note["problem"]))
            lines.append("  Ответ планировщика: " + note["answer"])
        lines.append("")
    if value["plan"].get("overlaps"):
        lines += ["## Пересечения с другими задачами"] + ["- " + item for item in value["plan"]["overlaps"]] + [""]
    lines.append("## Вехи")
    for item in document["milestones"]:
        flags = ", ".join(flag for flag, on in (("сложная", item["complexity"] == "complex"), ("интерфейс", item["ui"]),
                                                ("параллельно", item["parallel"])) if on)
        after = " после " + ", ".join(item["depends_on"]) if item["depends_on"] else ""
        lines.append("### %s. %s%s%s" % (item["id"], item["title"], " (" + flags + ")" if flags else "", after))
        lines.append(item["goal"])
        for stage in item["stages"]:
            lines.append("- **%s** %s (~%d строк, файлов: %d)" % (stage["id"], stage["brief"], stage["estimate_lines"],
                                                                len(stage["files"]) + len(stage["new_files"])))
            for criterion in stage["criteria"]:
                lines.append("  - [%s] %s" % ("браузер" if criterion["check"] == "behavior" else "код", criterion["text"]))
        lines.append("")
    if document["assumptions"]:
        lines += ["## Допущения"] + ["- " + item for item in document["assumptions"]] + [""]
    if document["rules"]:
        lines += ["## Правила проекта для исполнителя"] + ["- " + item for item in document["rules"]] + [""]
    lines.append("Раскладка: " + ", ".join("%s → %s" % (role, entry["model"] + (" " + entry["effort"] if entry.get("effort") else ""))
                                         for role, entry in value["cast"].items()))
    return "\n".join(lines)
