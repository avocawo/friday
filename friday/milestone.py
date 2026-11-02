"""Run a task's milestones: one executor session per milestone, gate, review by a stronger model, disputes to the owner."""
import json
import subprocess
from pathlib import Path

from . import card as cards
from . import gate, models, runner
from .errors import FridayError

GIT_COMMANDS = ["git add", "git commit", "git status", "git diff", "git log", "git restore"]
ROUNDS = 4

STRINGS = {"type": "array", "items": {"type": "string"}}
EXECUTOR_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "stages_done", "extra_files", "disputes"],
    "properties": {
        "summary": {"type": "string"}, "stages_done": STRINGS,
        "extra_files": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["path", "reason"],
                                                   "properties": {"path": {"type": "string"}, "reason": {"type": "string"}}}},
        "disputes": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["finding", "reason", "evidence"],
                                                "properties": {"finding": {"type": "integer"}, "reason": {"type": "string"},
                                                               "evidence": {"type": "string"}}}}}}
REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdict", "criteria_results", "findings"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "criteria_results": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                        "required": ["criterion", "status", "evidence"],
                                                        "properties": {"criterion": {"type": "string"},
                                                                       "status": {"type": "string", "enum": ["pass", "fail", "unverifiable"]},
                                                                       "evidence": {"type": "string"}}}},
        "findings": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                "required": ["severity", "file", "problem", "evidence"],
                                                "properties": {"severity": {"type": "string", "enum": ["blocking", "minor"]},
                                                               "file": {"type": "string"}, "problem": {"type": "string"},
                                                               "evidence": {"type": "string"}}}}}}

EXECUTOR_RULES = (
    "You implement one milestone of an approved plan in this repository. Work through the stages in order. After each "
    "stage run the checks available to you, fix what they report, then commit only that stage's files with "
    "git add <files> and git commit -m \"{key} <stage id>: <short summary>\". Follow the project rules below; they are "
    "a digest made for you, read the source only when a rule is unclear. Stay within the milestone's files; if you must "
    "change another file, do it and list it in extra_files with the reason. You have no network. Do not push, do not "
    "change git configuration, do not delete or rewrite earlier commits. Write summary in the task's language.")
REVIEW_RULES = (
    "You review one milestone of an approved plan. You get the milestone's goal, criteria and the project rules digest, "
    "the diff of this milestone only, and the result of the isolated gate (lint, typecheck, tests) on the same commit. "
    "You may read the repository for context. Judge every criterion as pass, fail or unverifiable with concrete evidence "
    "(file and line). Report every actionable defect in one pass: blocking for wrong behaviour, broken criteria, breaches "
    "of project rules or missing tests; minor for anything that can wait. Do not report style the linter accepts. "
    "verdict is pass when no blocking finding remains. Do not edit files. Write problems and evidence in the task's language.")


def git(worktree, *args):
    # gate.git disables hooks, fsmonitor, external diff drivers and textconv, so repository config cannot run code here.
    return gate.git(worktree, *args).decode("utf-8", "replace")


def identity(worktree):
    """The owner's git identity, passed explicitly: the executor's sandbox cannot read ~/.config/git."""
    env = {}
    for key, names in (("user.name", ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME")), ("user.email", ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"))):
        result = subprocess.run(["git", "-C", str(worktree), "config", key], capture_output=True, text=True)
        value = result.stdout.strip() or ("Friday" if key == "user.name" else "friday@localhost")
        env.update({name: value for name in names})
    return env


def load_plan(root, key):
    return json.loads((cards.directory(root, key) / "plan.json").read_text(encoding="utf-8"))


def define(document, milestone_id):
    for item in document["milestones"]:
        if item["id"] == milestone_id:
            return item
    raise FridayError("not_found", "Unknown milestone: " + milestone_id)


def set_state(root, key, milestone_id, **changes):
    def change(value):
        for item in value["milestones"]:
            if item["id"] == milestone_id:
                item.update(changes)
                return
        raise FridayError("not_found", "Unknown milestone: " + milestone_id)
    cards.update(root, key, change)


def milestone_text(key, document, item, decisions):
    value = {"task": key, "summary": document["summary"], "milestone": {k: item[k] for k in ("id", "title", "goal", "files")},
             "stages": item["stages"], "project_rules": document["rules"], "assumptions": document["assumptions"]}
    if decisions:
        value["owner_decisions"] = decisions
    return json.dumps(value, ensure_ascii=False, indent=2)


def numbered(findings):
    return "\n".join("%d. [%s] %s: %s Evidence: %s" % (index, item["severity"], item["file"], item["problem"], item["evidence"])
                     for index, item in enumerate(findings))


def run_role(root, key, profile, role, entry, worktree, prompt, schema, milestone_id, *, write=False, resume=None):
    commands = list(profile.get("executor_commands", [])) + GIT_COMMANDS if write else []
    spec = runner.Spec(driver=entry["driver"], model=entry["model"], effort=entry.get("effort"), prompt=prompt, cwd=worktree,
                       log_dir="", write=write, commands=commands, schema=schema, resume=resume,
                       env=dict(models.environment(entry, profile), **(identity(worktree) if write else {})),
                       deny_read=profile.get("executor_deny_read", []))
    return cards.run_agent(root, key, spec, role=role, milestone=milestone_id, phase=role, entry=entry, profile=profile)


def run(root, key, profile, milestone_id):
    """Drive one milestone to done. Returns its final record; raises only when the work cannot pass the gate."""
    card = cards.read(root, key)
    worktree, layout = card["worktree"], card["cast"]
    document = load_plan(root, key)
    item = define(document, milestone_id)
    start = git(worktree, "rev-parse", "HEAD").strip()
    reopened = any(entry["id"] == milestone_id and entry["state"] == "reopened" for entry in card["milestones"])
    set_state(root, key, milestone_id, state="running", start=start)
    cards.record(root, key, "milestone_started", milestone=milestone_id, start=start)
    available = models.availability(root)
    entry, note = models.executor(profile, layout, item["complexity"], available=available)
    if note:
        cards.record(root, key, "cast_note", milestone=milestone_id, note=note)
    allowed = list(profile.get("executor_commands", [])) + GIT_COMMANDS
    base_prompt = (EXECUTOR_RULES.format(key=key) + "\n\nShell commands you may run, each starting exactly like one of "
                   "these (anything else is denied; run one command per call, without pipes or redirection):\n" +
                   "\n".join("- " + command for command in allowed) + "\n\n" +
                   milestone_text(key, document, item, card["decisions"]))
    if reopened:
        base_prompt += ("\n\nThis milestone was implemented before; the owner's latest decision changes it. Update the "
                        "existing implementation on this branch to match the decision instead of starting over.")
    prompt, session, failures, findings, disputes, gate_record, extra = base_prompt, None, 0, [], [], None, []
    review_value, review_entry, feedback_text, checked_head = None, None, "", None
    for round_number in range(1, profile.get("milestone_rounds", ROUNDS) + 1):
        result = run_role(root, key, profile, "develop", entry, worktree, prompt, EXECUTOR_SCHEMA, milestone_id, write=True, resume=session)
        if result.status == "quota":
            available = models.availability(root)
            entry, note = models.executor(profile, layout, item["complexity"], failures, entry, available)
            cards.record(root, key, "cast_note", milestone=milestone_id, note=note or "квота исполнителя")
            prompt, session = base_prompt + ("\n\nPrevious attempt feedback:\n" + feedback_text if round_number > 1 else ""), None
            continue
        if result.status != "ok":
            raise FridayError("agent_failed", "Executor failed on %s (%s): %s" % (milestone_id, result.status, result.error))
        session = result.session
        answer = result.structured or {}
        extra = answer.get("extra_files", extra)
        for dispute in answer.get("disputes", []):
            if 0 <= dispute["finding"] < len(findings):
                disputes.append(dict(findings[dispute["finding"]], milestone=milestone_id, executor_reason=dispute["reason"],
                                     executor_evidence=dispute["evidence"], reviewer=review_entry["model"], status="owner"))
        if git(worktree, "status", "--porcelain"):
            git(worktree, "add", "-A")
            names = identity(worktree)
            git(worktree, "-c", "user.name=" + names["GIT_AUTHOR_NAME"], "-c", "user.email=" + names["GIT_AUTHOR_EMAIL"],
                "commit", "-qm", "%s %s: changes left uncommitted by the executor" % (key, milestone_id))
            cards.record(root, key, "uncommitted_changes_committed", milestone=milestone_id)
        head = git(worktree, "rev-parse", "HEAD").strip()
        disputed = {(d["file"], d["problem"]) for d in disputes}
        if head == checked_head and findings and all((f["file"], f["problem"]) in disputed for f in findings):
            # Every remaining finding is disputed and nothing changed: the reviewed commit stands, the owner decides.
            return close(root, key, milestone_id, item, start, worktree, review_value, gate_record, disputes, extra, round_number)
        if head == start or head == checked_head:
            failures += 1
            feedback_text = ("You made no commits. Implement the stages and commit each one." if head == start else
                             "You made no new commit, so nothing changed since the last check. Address the feedback and commit.")
        else:
            checked_head = head
            gate_record = None
            if profile.get("gate"):
                log = cards.directory(root, key) / "gates" / ("%s-%d.log" % (milestone_id, round_number))
                gate_record = gate.check(root, profile, worktree, card.get("base"), log)
                cards.record(root, key, "gate", milestone=milestone_id, status=gate_record["status"], head=gate_record["head"])
                if gate_record["status"] == "environment_unavailable":
                    set_state(root, key, milestone_id, state="blocked")
                    raise FridayError("environment_unavailable", "The gate environment is unavailable; see " + gate_record["log"])
            if gate_record and gate_record["status"] != "passed":
                failures += 1
                feedback_text = "The isolated gate failed on your last commit. Fix these errors:\n\n" + gate.failures(
                    Path(gate_record["log"]).read_text(encoding="utf-8", errors="replace"))
            else:
                for _ in range(2):  # a reviewer out of quota is replaced once by the next model the rules allow
                    review_entry, note = models.reviewer(profile, layout, "review", entry, models.availability(root))
                    if note:
                        cards.record(root, key, "cast_note", milestone=milestone_id, note=note)
                    review_value = review(root, key, profile, review_entry, worktree, document, item, start, gate_record, findings, disputes)
                    if review_value is not None:
                        break
                if review_value is None:
                    raise FridayError("agent_failed", "No reviewer with quota is available for " + milestone_id)
                # Exact match only: a rephrased repeat of a disputed finding goes back to the executor once more.
                disputed = {(d["file"], d["problem"]) for d in disputes}
                findings = [finding for finding in review_value["findings"]
                            if finding["severity"] == "blocking" and (finding["file"], finding["problem"]) not in disputed]
                if not findings:
                    return close(root, key, milestone_id, item, start, worktree, review_value, gate_record, disputes, extra, round_number)
                failures += 1
                feedback_text = ("A reviewer of another vendor found blocking problems. Fix each one, or dispute it in disputes "
                                 "with the finding number, a reason and evidence (rule path and quote, or code location). "
                                 "Disputed findings go to the owner.\n\n" + numbered(findings))
        if failures >= 2:
            escalated, note = models.executor(profile, layout, item["complexity"], failures, entry, available)
            if escalated["name"] != entry["name"]:
                cards.record(root, key, "cast_note", milestone=milestone_id, note=note)
                entry, session, failures = escalated, None, 0
                prompt = base_prompt + "\n\nA previous executor left commits on this branch. Continue from them.\n" + feedback_text
                continue
        prompt = feedback_text
    if review_value is None:
        reason = "no commits" if git(worktree, "rev-parse", "HEAD").strip() == start else "the gate never passed"
        set_state(root, key, milestone_id, state="failed")
        cards.record(root, key, "milestone_failed", milestone=milestone_id, reason=reason)
        raise FridayError("milestone_failed", "Milestone %s was never reviewed: %s." % (milestone_id, reason))
    if gate_record is None or gate_record["status"] == "passed":
        # The pipeline does not stop on review disagreement: what remains goes to the owner with the draft.
        for finding in findings:
            disputes.append(dict(finding, milestone=milestone_id, executor_reason="Не исправлено за отведённые круги.",
                                 executor_evidence="", reviewer=review_entry["model"], status="owner"))
        return close(root, key, milestone_id, item, start, worktree, review_value, gate_record, disputes, extra, ROUNDS)
    set_state(root, key, milestone_id, state="failed")
    cards.record(root, key, "milestone_failed", milestone=milestone_id, gate=gate_record["log"])
    raise FridayError("milestone_failed", "Milestone %s still fails the gate after all rounds; see %s" % (milestone_id, gate_record["log"]))


def review(root, key, profile, entry, worktree, document, item, start, gate_record, previous, disputes):
    diff = git(worktree, "diff", start + "..HEAD")
    gate_text = ("not configured" if gate_record is None else "%s on %s (command: %s)" % (
        gate_record["status"], gate_record["head"], " ".join(profile.get("gate", []))))
    prompt = (REVIEW_RULES + "\n\n" + milestone_text(key, document, item, cards.read(root, key)["decisions"]) +
              "\n\nGate: " + gate_text + "\n\nDiff of this milestone:\n" + diff)
    if previous:
        prompt += "\n\nYour previous blocking findings, which the executor has now addressed:\n" + numbered(previous)
    if disputes:
        prompt += ("\n\nFindings the executor disputed; they already go to the owner, do not repeat them without new evidence:\n" +
                   "\n".join("- %s: %s — %s" % (d["file"], d["problem"], d["executor_reason"]) for d in disputes))
    result = run_role(root, key, profile, "review", entry, worktree, prompt, REVIEW_SCHEMA, item["id"])
    if result.status == "quota":
        return None
    if result.status != "ok" or not isinstance(result.structured, dict):
        raise FridayError("agent_failed", "Review of %s failed (%s): %s" % (item["id"], result.status, result.error))
    value = result.structured
    cards.record(root, key, "milestone_reviewed", milestone=item["id"], verdict=value["verdict"], model=entry["model"],
                 blocking=sum(f["severity"] == "blocking" for f in value["findings"]),
                 unverifiable=sum(c["status"] == "unverifiable" for c in value["criteria_results"]))
    return value


def close(root, key, milestone_id, item, start, worktree, review_value, gate_record, disputes, extra, rounds):
    commits = [line.split(" ", 1) for line in git(worktree, "log", "--format=%H %s", start + "..HEAD").splitlines()]
    stages = []
    for stage in item["stages"]:
        matching = [sha for sha, subject in commits if (" " + stage["id"] + ":") in (" " + subject)]
        stages.append({"id": stage["id"], "commit": matching[0] if matching else None})
    changed = set(filter(None, git(worktree, "diff", "--name-only", start + "..HEAD").splitlines()))
    outside = sorted(changed - set(item["files"]))
    minor = [f for f in (review_value or {}).get("findings", []) if f["severity"] == "minor"]
    set_state(root, key, milestone_id, state="done", stages=stages, head=git(worktree, "rev-parse", "HEAD").strip(),
              outside_files=outside, minor_findings=minor)
    if disputes:
        cards.update(root, key, lambda value: value["disputes"].extend(disputes))
    cards.record(root, key, "milestone_done", milestone=milestone_id, rounds=rounds, commits=len(commits),
                 disputes=len(disputes), outside_files=outside, extra_files=[e["path"] for e in extra])
    return {"milestone": milestone_id, "commits": len(commits), "disputes": len(disputes), "outside_files": outside}


def ready(card):
    done = {item["id"] for item in card["milestones"] if item["state"] == "done"}
    return [item["id"] for item in card["milestones"] if item["state"] in ("pending", "reopened")
            and set(item["depends_on"]) <= done]


def run_task(root, key, profile):
    """Run every milestone whose dependencies are done, in plan order, until none is left."""
    card = cards.read(root, key)
    if card["state"] not in ("approved", "running", "stopped") or not card["plan"]["approved"]:
        raise FridayError("invalid_state", "Approve the plan of %s before running it." % key)
    cards.update(root, key, lambda value: value.update(state="running"))
    cards.record(root, key, "run_task_started")
    completed = []
    try:
        while True:
            pending = ready(cards.read(root, key))
            if not pending:
                break
            completed.append(run(root, key, profile, pending[0]))
    except FridayError:
        cards.update(root, key, lambda value: value.update(state="failed"))
        raise
    except BaseException:
        cards.update(root, key, lambda value: value.update(state="stopped"))
        raise
    left = [item["id"] for item in cards.read(root, key)["milestones"] if item["state"] != "done"]
    cards.update(root, key, lambda value: value.update(state="done" if not left else "stopped"))
    cards.record(root, key, "run_task_finished", completed=[item["milestone"] for item in completed], left=left)
    return {"completed": completed, "left": left}


def decide(root, key, name, text, reopen=()):
    """Record an owner decision; only the milestones the owner names (and those after them) are reopened."""
    def change(value):
        ids = {item["id"] for item in value["milestones"]}
        unknown = set(reopen) - ids
        if unknown:
            raise FridayError("not_found", "Unknown milestones: " + ", ".join(sorted(unknown)))
        value["decisions"].append({"name": name, "text": text, "reopen": list(reopen)})
        affected = set(reopen)
        for item in value["milestones"]:
            if set(item["depends_on"]) & affected and item["state"] == "done":
                affected.add(item["id"])
        for item in value["milestones"]:
            if item["id"] in affected and item["state"] == "done":
                item["state"] = "reopened"
        if affected and value["state"] == "done":
            value["state"] = "stopped"  # reopened work makes the task runnable again
        return sorted(affected)
    affected = cards.update(root, key, change)
    cards.record(root, key, "decision", name=name, reopened=affected)
    return affected
