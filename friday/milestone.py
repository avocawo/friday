"""Run a task's milestones: one executor session per milestone, gate, review by a stronger model, disputes to the owner."""
import json
import os
import signal
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from . import card as cards
from . import design, final, gate, models, runner, stories, workspace
from .errors import FridayError

GIT_COMMANDS = ["git add", "git commit", "git status", "git diff", "git log", "git restore"]
ROUNDS = 4
PARALLEL_LIMIT = 2

STRINGS = {"type": "array", "items": {"type": "string"}}
EXECUTOR_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "stages_done", "extra_files", "disputes", "checks",
                                                                 "checks_note"],
    "properties": {
        "summary": {"type": "string"}, "stages_done": STRINGS,
        "checks": {"type": "string", "enum": ["passed", "failed", "not_run"]}, "checks_note": {"type": "string"},
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
    "change git configuration, do not delete or rewrite earlier commits. Report your checks honestly: checks is passed "
    "when every available check ran on your last commit and passed, failed when a check ran and reported errors, not_run "
    "when a check could not run at all (missing dependencies, a crashing tool, no commands available); say which and why "
    "in checks_note. Write summary and checks_note in the task's language.")
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
    if item.get("ui") and document.get("design"):
        value["design_spec"] = document["design"]
    if decisions:
        value["owner_decisions"] = decisions
    return json.dumps(value, ensure_ascii=False, indent=2)


def numbered(findings):
    return "\n".join("%d. [%s] %s: %s Evidence: %s" % (index, item["severity"], item["file"], item["problem"], item["evidence"])
                     for index, item in enumerate(findings))


def run_role(root, key, profile, role, entry, worktree, prompt, schema, milestone_id, *, write=False, resume=None, mcp=None):
    if write:
        models.require_executor(profile, entry)
    commands = list(profile.get("executor_commands", [])) + GIT_COMMANDS + stories.executor_commands(profile) if write else []
    card = cards.read(root, key)
    showcase = stories.worktree(card) if worktree == card.get("worktree") else None
    env = dict(models.environment(entry, profile), **(identity(worktree) if write else {}))
    if mcp:
        env["MCP_TIMEOUT"] = "180000"  # the preview browser takes a while to start
    spec = runner.Spec(driver=entry["driver"], model=entry["model"], effort=entry.get("effort"), prompt=prompt, cwd=worktree,
                       log_dir="", write=write, commands=commands, schema=schema, resume=resume,
                       env=env, deny_read=profile.get("executor_deny_read", []), mcp=mcp,
                       add_dirs=[showcase] if showcase else [])
    return cards.run_agent(root, key, spec, role=role, milestone=milestone_id, phase=role, entry=entry, profile=profile)


def run(root, key, profile, milestone_id, worktree=None):
    """Drive one milestone to done. Returns its final record; raises only when the work cannot pass the gate."""
    card = cards.read(root, key)
    worktree, layout = worktree or card["worktree"], card["cast"]
    document = load_plan(root, key)
    item = define(document, milestone_id)
    start = git(worktree, "rev-parse", "HEAD").strip()
    showcase_start = stories.head(card)
    def mark():  # progress is a commit in the kit or in the showcase
        return git(worktree, "rev-parse", "HEAD").strip() + ("+" + stories.head(card) if showcase_start else "")
    start_mark = mark()
    reopened = any(entry["id"] == milestone_id and entry["state"] == "reopened" for entry in card["milestones"])
    set_state(root, key, milestone_id, state="running", start=start, **({"showcase_start": showcase_start} if showcase_start else {}))
    cards.record(root, key, "milestone_started", milestone=milestone_id, start=start)
    available = models.availability(root)
    entry, note = models.executor(profile, layout, item["complexity"], available=available)
    if note:
        cards.record(root, key, "cast_note", milestone=milestone_id, note=note)
    allowed = list(profile.get("executor_commands", [])) + GIT_COMMANDS + stories.executor_commands(profile)
    base_prompt = (EXECUTOR_RULES.format(key=key) + design.guidance(profile, "develop", document, item["ui"]) +
                   stories.executor_text(profile, card) +
                   "\n\nShell commands you may run, each starting exactly like one of "
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
        if answer.get("summary"):
            set_state(root, key, milestone_id, summary=answer["summary"], executor=entry["model"])
        checks, checks_note = answer.get("checks", "passed"), answer.get("checks_note", "")
        set_state(root, key, milestone_id, checks=checks, checks_note=checks_note)
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
        if stories.commit_leftovers(card, key, milestone_id, identity(worktree)):
            cards.record(root, key, "uncommitted_changes_committed", milestone=milestone_id, repository="stories")
        head = mark()
        disputed = {(d["file"], d["problem"]) for d in disputes}
        if head == checked_head and findings and all((f["file"], f["problem"]) in disputed for f in findings):
            # Every remaining finding is disputed and nothing changed: the reviewed commit stands, the owner decides.
            return close(root, key, milestone_id, item, start, worktree, review_value, gate_record, disputes, extra, round_number)
        if head == start_mark or head == checked_head:
            failures += 1
            feedback_text = ("You made no commits. Implement the stages and commit each one." if head == start_mark else
                             "You made no new commit, so nothing changed since the last check. Address the feedback and commit.")
        else:
            checked_head = head
            gate_record = None
            if profile.get("gate"):
                log = cards.directory(root, key) / "gates" / ("%s-%d.log" % (milestone_id, round_number))
                gate_record = gate.check(root, profile, worktree, card.get("base"), log,
                                         on_wait=lambda name: cards.waiting(root, key, name, milestone_id))
                cards.stop_waiting(root, key, milestone_id)
                cards.record(root, key, "gate", milestone=milestone_id, status=gate_record["status"], head=gate_record["head"])
                if gate_record["status"] == "environment_unavailable":
                    set_state(root, key, milestone_id, state="blocked")
                    raise FridayError("environment_unavailable", "The gate environment is unavailable; see " + gate_record["log"])
            elif checks == "not_run" and profile.get("executor_commands"):
                # Nothing else checks this commit: a broken environment blocks the milestone instead of passing it unchecked.
                set_state(root, key, milestone_id, state="blocked", blocked_reason=checks_note)
                cards.record(root, key, "milestone_blocked", milestone=milestone_id, reason=checks_note)
                raise FridayError("environment_unavailable", "The executor's checks could not run on %s and there is no gate: %s"
                                  % (milestone_id, checks_note))
            if gate_record is None and checks == "failed":
                failures += 1
                feedback_text = ("Your own checks fail on your last commit and no gate checks it instead. Fix the errors "
                                 "they report and commit:\n\n" + checks_note)
            elif gate_record and gate_record["status"] != "passed":
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
                source = "A reviewer of another vendor found blocking problems."
                if not findings and item["ui"]:
                    findings = [finding for finding in accept_in_browser(root, key, profile, layout, entry, worktree, item)
                                if (finding["file"], finding["problem"]) not in disputed]
                    source = "The browser acceptance check saw these criteria fail in the running application."
                if not findings:
                    return close(root, key, milestone_id, item, start, worktree, review_value, gate_record, disputes, extra, round_number)
                failures += 1
                feedback_text = (source + " Fix each one, or dispute it in disputes with the finding number, a reason and "
                                 "evidence (rule path and quote, or code location). Disputed findings go to the owner.\n\n" +
                                 numbered(findings))
        if failures >= 2:
            escalated, note = models.executor(profile, layout, item["complexity"], failures, entry, available)
            if escalated["name"] != entry["name"]:
                cards.record(root, key, "cast_note", milestone=milestone_id, note=note)
                entry, session, failures = escalated, None, 0
                prompt = base_prompt + "\n\nA previous executor left commits on this branch. Continue from them.\n" + feedback_text
                continue
        prompt = feedback_text
    if review_value is None:
        reason = "no commits" if mark() == start_mark else ("the gate never passed" if profile.get("gate") else "no check ever passed")
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


def accept_in_browser(root, key, profile, layout, author, worktree, item):
    """Blocking findings from the browser acceptance of a UI milestone; an unverifiable check is noted, not blocking."""
    entry, note = models.reviewer(profile, layout, "browser", author, models.availability(root))
    if note:
        cards.record(root, key, "cast_note", milestone=item["id"], note=note)
    result = final.browser(root, key, profile, entry, worktree, item, run_role, REVIEW_SCHEMA)
    if result is None:
        set_state(root, key, item["id"], browser={"status": "skipped", "reason": "в профиле нет браузера или preview"})
        return []
    if result.status != "ok" or not isinstance(result.structured, dict):
        cards.record(root, key, "browser_unverifiable", milestone=item["id"], reason=result.error or result.status)
        set_state(root, key, item["id"], browser={"status": "unverifiable", "reason": (result.error or result.status)[:300]})
        return []
    value = result.structured
    statuses = [c["status"] for c in value["criteria_results"]]
    themes = stories.theme_results(value["criteria_results"], profile)
    cards.record(root, key, "browser_checked", milestone=item["id"], verdict=value["verdict"], model=entry["model"],
                 unverifiable=statuses.count("unverifiable"), **({"themes": themes} if themes else {}))
    # Minor browser findings (a design difference seen only by eye) go to the owner with the draft, not to the executor.
    set_state(root, key, item["id"], browser_minor=[f for f in value["findings"] if f["severity"] == "minor"])
    if not statuses or all(status == "unverifiable" for status in statuses):
        cards.record(root, key, "browser_unverifiable", milestone=item["id"], reason="no criterion could be observed")
        set_state(root, key, item["id"], browser={"status": "unverifiable", "reason": "ни один критерий не удалось наблюдать"})
    elif themes and any(result == "unverifiable" for result in themes.values()):
        missing = [name for name, result in themes.items() if result == "unverifiable"]
        set_state(root, key, item["id"], themes=themes, browser={"status": "unverifiable",
                                                                 "reason": "не проверены темы: " + ", ".join(missing)})
    else:
        set_state(root, key, item["id"], browser={"status": "checked", "reason": ""}, **({"themes": themes} if themes else {}))
    return [finding for finding in value["findings"] if finding["severity"] == "blocking"]


def review(root, key, profile, entry, worktree, document, item, start, gate_record, previous, disputes):
    diff = git(worktree, "diff", start + "..HEAD")
    card = cards.read(root, key)
    showcase = stories.diff(card, next((m.get("showcase_start") for m in card["milestones"] if m["id"] == item["id"]), None))
    if showcase:
        diff += "\n\nDiff of the showcase repository (paths are %s…):\n%s" % (stories.PREFIX, showcase)
    gate_text = ("not configured" if gate_record is None else "%s on %s (command: %s)" % (
        gate_record["status"], gate_record["head"], " ".join(profile.get("gate", []))))
    prompt = (REVIEW_RULES + design.guidance(profile, "review", document, item["ui"]) + "\n\n" + milestone_text(key, document, item, cards.read(root, key)["decisions"]) +
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
    card = cards.read(root, key)
    showcase_start = next((m.get("showcase_start") for m in card["milestones"] if m["id"] == milestone_id), None)
    showcase_commits, showcase_changed = [], set()
    if stories.worktree(card) and showcase_start:
        showcase_commits = [line.split(" ", 1) for line in stories.git(card, "log", "--format=%H %s", showcase_start + "..HEAD").splitlines()]
        showcase_changed = {stories.PREFIX + name for name in stories.git(card, "diff", "--name-only", showcase_start + "..HEAD").splitlines() if name}
    stages = []
    for stage in item["stages"]:
        matching = [sha for sha, subject in commits if (" " + stage["id"] + ":") in (" " + subject)]
        shown = [sha for sha, subject in showcase_commits if (" " + stage["id"] + ":") in (" " + subject)]
        stages.append(dict({"id": stage["id"], "commit": matching[0] if matching else None},
                           **({"showcase_commit": shown[0]} if shown else {})))
    changed = set(filter(None, git(worktree, "diff", "--name-only", start + "..HEAD").splitlines())) | showcase_changed
    outside = sorted(changed - set(item["files"]))
    minor = [f for f in (review_value or {}).get("findings", []) if f["severity"] == "minor"]
    reasons = unverified(item, next(m for m in card["milestones"] if m["id"] == milestone_id), gate_record)
    set_state(root, key, milestone_id, state="done", stages=stages, head=git(worktree, "rev-parse", "HEAD").strip(),
              **({"showcase_head": stories.head(card)} if stories.worktree(card) else {}),
              outside_files=outside, minor_findings=minor, verified=not reasons, unverified=reasons)
    if reasons:
        cards.record(root, key, "milestone_unverified", milestone=milestone_id, reasons=reasons)
    if disputes:
        cards.update(root, key, lambda value: value["disputes"].extend(disputes))
    cards.record(root, key, "milestone_done", milestone=milestone_id, rounds=rounds, commits=len(commits),
                 disputes=len(disputes), outside_files=outside, extra_files=[e["path"] for e in extra])
    return {"milestone": milestone_id, "commits": len(commits), "disputes": len(disputes), "outside_files": outside}


def unverified(item, entry, gate_record):
    """Why a finished milestone was not actually checked: no gate, the executor's own checks, the browser."""
    reasons = []
    gate_passed = bool(gate_record) and gate_record.get("status") == "passed"
    if gate_record is None:
        reasons.append("гейт не настроен: изолированной проверки не было")
    if not gate_passed and entry.get("checks", "passed") != "passed":
        reasons.append("проверки исполнителя %s: %s" % ({"failed": "не прошли", "not_run": "не запускались"}.get(
            entry["checks"], entry["checks"]), entry.get("checks_note") or "без пояснения"))
    browser = entry.get("browser") or {}
    if item.get("ui") and browser.get("status") != "checked":
        reasons.append("браузерная приёмка %s: %s" % ({"skipped": "не проводилась", "unverifiable": "не смогла проверить"}.get(
            browser.get("status"), "не проводилась"), browser.get("reason") or "нет данных"))
    return reasons


def task_unverified(card):
    return [{"milestone": item["id"], "reasons": item["unverified"]} for item in card["milestones"] if item.get("unverified")]


def ready(card):
    done = {item["id"] for item in card["milestones"] if item["state"] == "done"}
    return [item["id"] for item in card["milestones"] if item["state"] in ("pending", "reopened")
            and set(item["depends_on"]) <= done]


def run_task(root, key, profile):
    """Run every milestone whose dependencies are done, in plan order, until none is left."""
    card = cards.read(root, key)
    if card["state"] not in ("approved", "running", "stopped") or not card["plan"]["approved"]:
        raise FridayError("invalid_state", "Approve the plan of %s before running it." % key)
    for item in card["milestones"]:
        if item.get("recovery_worktree") and (os.path.lexists(item["recovery_worktree"]) or
                git(profile["repository"], "branch", "--list", item["recovery_branch"]).strip()):
            raise FridayError("parallel_recovery_required", "Inspect retained work for %s before resuming: %s (%s)."
                              % (item["id"], item["recovery_worktree"], item["recovery_branch"]))
    cards.update(root, key, lambda value: value.update(state="running", runner_pid=os.getpid()))
    cards.record(root, key, "run_task_started")
    previous = None
    if threading.current_thread() is threading.main_thread():
        def interrupt(signum, frame):
            raise runner.Interrupted("Stopped by signal %d" % signum)
        previous = signal.signal(signal.SIGTERM, interrupt)  # friday stop: finish cleanly, never leave agents behind
    completed = []
    try:
        runner.CANCEL.clear()
        document = load_plan(root, key)
        while True:
            pending = ready(cards.read(root, key))
            if not pending:
                break
            group = [name for name in pending if define(document, name)["parallel"]][:profile.get("max_parallel_milestones", PARALLEL_LIMIT)]
            if len(group) >= 2:
                completed.extend(run_parallel(root, key, profile, group))
            else:
                completed.append(run(root, key, profile, pending[0]))
    except FridayError:
        cards.update(root, key, lambda value: value.update(state="failed"))
        raise
    except BaseException:
        runner.CANCEL.set()
        cards.update(root, key, lambda value: value.update(state="stopped"))
        cards.record(root, key, "stopped")
        raise
    finally:
        runner.CANCEL.clear()  # every lane has stopped by now; later runs in this process start fresh
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    left = [item["id"] for item in cards.read(root, key)["milestones"] if item["state"] != "done"]
    delivery = None
    if not left:
        try:
            delivery = finish(root, key, profile)
        except FridayError:
            cards.update(root, key, lambda value: value.update(state="failed"))
            raise
    cards.update(root, key, lambda value: value.update(state="done" if not left else "stopped", unverified=task_unverified(value)))
    cards.record(root, key, "run_task_finished", completed=[item["milestone"] for item in completed], left=left)
    return {"completed": completed, "left": left, "delivery": delivery}


FIX_RULES = (
    "The final review of the whole task found blocking problems. Fix each one in this repository, run your checks and "
    "commit with git commit -m \"{key} final: <short summary>\", or dispute a finding in disputes with its number, a reason "
    "and evidence. Disputed findings go to the owner. Follow the project rules digest.")


def finish(root, key, profile):
    """Final review and blind check, one fix round for blocking findings, then the draft merge request."""
    cards.update(root, key, lambda value: value.update(unverified=task_unverified(value)))
    card = cards.read(root, key)
    worktree, layout = card["worktree"], card["cast"]
    cards.record(root, key, "final_started")
    results, final_entry = final.final_review(root, key, profile, run_role, REVIEW_SCHEMA)
    blocking = [f for value in results.values() for f in value["findings"] if f["severity"] == "blocking"]
    minor = [f for value in results.values() for f in value["findings"] if f["severity"] == "minor"]
    cards.update(root, key, lambda value: value.update(final_minor=minor))
    if blocking:
        document = load_plan(root, key)
        entry, note = models.executor(profile, layout, available=models.availability(root))
        if note:
            cards.record(root, key, "cast_note", milestone="final", note=note)
        allowed = list(profile.get("executor_commands", [])) + GIT_COMMANDS + stories.executor_commands(profile)
        prompt = (FIX_RULES.format(key=key) + stories.executor_text(profile, card) + "\n\nShell commands you may run, each starting exactly like one of these:\n" +
                  "\n".join("- " + command for command in allowed) + "\n\nProject rules:\n" + "\n".join(document["rules"]) +
                  "\n\nFindings:\n" + numbered(blocking))
        result = run_role(root, key, profile, "develop", entry, worktree, prompt, EXECUTOR_SCHEMA, "final", write=True)
        disputes = []
        for dispute in (result.structured or {}).get("disputes", []):
            if 0 <= dispute["finding"] < len(blocking):
                disputes.append(dict(blocking[dispute["finding"]], milestone="final", executor_reason=dispute["reason"],
                                     executor_evidence=dispute["evidence"], reviewer=final_entry["model"], status="owner"))
        if git(worktree, "status", "--porcelain"):
            names = identity(worktree)
            git(worktree, "add", "-A")
            git(worktree, "-c", "user.name=" + names["GIT_AUTHOR_NAME"], "-c", "user.email=" + names["GIT_AUTHOR_EMAIL"],
                "commit", "-qm", "%s final: changes left uncommitted by the executor" % key)
        stories.commit_leftovers(card, key, "final", identity(worktree))
        if profile.get("gate"):
            log = cards.directory(root, key) / "gates" / "final.log"
            record = gate.check(root, profile, worktree, card.get("base"), log,
                                on_wait=lambda name: cards.waiting(root, key, name, "final"))
            cards.stop_waiting(root, key, "final")
            cards.record(root, key, "gate", milestone="final", status=record["status"], head=record["head"])
            if record["status"] != "passed":
                raise FridayError("final_failed", "The gate fails after the final fixes; see " + record["log"])
        again, _ = final.final_review(root, key, profile, run_role, REVIEW_SCHEMA, roles=("final-review",))
        disputed = {(d["file"], d["problem"]) for d in disputes}
        remaining = [f for f in again["final-review"]["findings"] if f["severity"] == "blocking" and (f["file"], f["problem"]) not in disputed]
        disputes += [dict(f, milestone="final", executor_reason="Не исправлено после финального ревью.", executor_evidence="",
                          reviewer=final_entry["model"], status="owner") for f in remaining]
        if disputes:
            cards.update(root, key, lambda value: value["disputes"].extend(disputes))
    return final.deliver(root, key, profile)


def parallel_overlap(changes):
    """Path aliases and file/directory collisions also share a write surface."""
    owners = []
    shared = set()
    for name, paths in changes.items():
        for path in {str(PurePosixPath(path)) for path in paths}:
            for owner, other in owners:
                if owner != name and (path == other or path.startswith(other + "/") or other.startswith(path + "/")):
                    shared.add(path if path == other else path + " <-> " + other)
            owners.append((name, path))
    return sorted(shared)


def changed_paths(worktree, start):
    paths = set()
    for commit in git(worktree, "rev-list", start + "..HEAD").split():
        if len(git(worktree, "rev-list", "--parents", "-n", "1", commit).split()) > 2:
            raise FridayError("parallel_merge", "A parallel lane contains a merge commit; inspect it before integration.")
        paths.update(filter(None, git(worktree, "diff-tree", "--no-commit-id", "--name-only", "--no-renames",
                                      "-r", "-z", commit).split("\0")))
    return paths


def run_parallel(root, key, profile, group):
    """Milestones the plan marks parallel (no shared files) run at once, each in its own worktree; their commits
    are then cherry-picked onto the task branch in plan order."""
    card = cards.read(root, key)
    document = load_plan(root, key)
    overlap = parallel_overlap({name: define(document, name)["files"] for name in group})
    if overlap:
        raise FridayError("parallel_overlap", "Parallel milestones declare shared paths: " + ", ".join(overlap))
    main, branch, repo = card["worktree"], card["branch"], profile["repository"]
    lanes = {}
    lane_base = git(main, "rev-parse", "HEAD").strip()
    for name in group:
        path, lane = str(Path(main).parent / (Path(main).name + "--" + name)), branch + "--" + name
        if os.path.lexists(path) or git(repo, "branch", "--list", lane).strip():
            raise FridayError("parallel_recovery_required", "Inspect retained parallel work before reusing %s (%s)." % (path, lane))
        workspace.git(repo, "worktree", "add", "-q", "-b", lane, path, lane_base)
        lanes[name] = (path, lane)
    cards.record(root, key, "parallel_started", milestones=group)
    results, errors = {}, {}
    pool = ThreadPoolExecutor(max_workers=min(len(group), profile.get("max_parallel_milestones", PARALLEL_LIMIT)))
    futures = {name: pool.submit(run, root, key, profile, name, lanes[name][0]) for name in group}
    try:
        for name, future in futures.items():
            try:
                results[name] = future.result()
            except FridayError as exc:
                errors[name] = exc
                set_state(root, key, name, state="failed", blocked_reason=str(exc))
    except BaseException:
        runner.CANCEL.set()  # Ctrl-C or SIGTERM: stop the other lanes' agents too
        pool.shutdown(wait=True)
        for name, (path, lane) in lanes.items():
            set_state(root, key, name, state="failed", recovery_worktree=path, recovery_branch=lane)
            cards.record(root, key, "parallel_lane_retained", milestone=name, worktree=path, branch=lane)
        raise
    pool.shutdown(wait=True)
    retained = {name for name in errors if git(lanes[name][0], "rev-parse", "HEAD").strip() != lane_base
                or git(lanes[name][0], "status", "--porcelain").strip()}
    integrated = set()
    try:
        overlap = parallel_overlap({name: changed_paths(lanes[name][0],
            next(item["start"] for item in cards.read(root, key)["milestones"] if item["id"] == name)) for name in results})
        if overlap:
            cards.record(root, key, "parallel_overlap", files=overlap, lanes={name: path for name, (path, _) in lanes.items()})
            raise FridayError("parallel_overlap", "Parallel milestones changed shared paths: %s. Lanes retained for recovery."
                              % ", ".join(overlap))
        for name in group:
            if name in results:
                integrate(root, key, profile, name, main, lanes[name][0])
                integrated.add(name)
    except BaseException:
        retained.update(set(results) - integrated)
        for name in set(results) - integrated:
            set_state(root, key, name, state="failed", blocked_reason="Parallel work was not integrated; inspect its retained lane.")
        raise
    finally:
        for name, (path, lane) in lanes.items():
            if name in retained:
                set_state(root, key, name, recovery_worktree=path, recovery_branch=lane)
                cards.record(root, key, "parallel_lane_retained", milestone=name, worktree=path, branch=lane)
            else:
                workspace.git(repo, "worktree", "remove", "--force", path)
                workspace.git(repo, "branch", "-D", lane)
    if errors:
        raise next(iter(errors.values()))
    return [results[name] for name in group]


def integrate(root, key, profile, name, main, lane):
    """Cherry-pick a parallel milestone's commits onto the task branch and point its stages at the new commits."""
    item = define(load_plan(root, key), name)
    start = next(entry["start"] for entry in cards.read(root, key)["milestones"] if entry["id"] == name)
    commits = git(lane, "rev-list", "--reverse", start + "..HEAD").split()
    before = git(main, "rev-parse", "HEAD").strip()
    names = identity(main)
    if commits:
        result = subprocess.run(["git", "-C", main, "-c", "core.hooksPath=/dev/null", "-c", "user.name=" + names["GIT_AUTHOR_NAME"],
                                 "-c", "user.email=" + names["GIT_AUTHOR_EMAIL"], "cherry-pick", *commits], capture_output=True, text=True)
        if result.returncode:
            subprocess.run(["git", "-C", main, "cherry-pick", "--abort"], capture_output=True)
            set_state(root, key, name, state="failed")
            raise FridayError("merge_conflict", "Parallel milestone %s conflicts with the task branch: %s" % (name, result.stderr.strip()))
    subjects = [line.split(" ", 1) for line in git(main, "log", "--format=%H %s", before + "..HEAD").splitlines()]
    stages = [{"id": stage["id"], "commit": next((sha for sha, subject in subjects if (" " + stage["id"] + ":") in (" " + subject)), None)}
              for stage in item["stages"]]
    set_state(root, key, name, stages=stages, head=git(main, "rev-parse", "HEAD").strip(), start=before)
    cards.record(root, key, "parallel_integrated", milestone=name, commits=len(commits))


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
                for field in ("verified", "unverified", "browser", "checks", "checks_note"):
                    item.pop(field, None)
        if affected and value["state"] == "done":
            value["state"] = "stopped"  # reopened work makes the task runnable again
        return sorted(affected)
    affected = cards.update(root, key, change)
    cards.record(root, key, "decision", name=name, reopened=affected)
    return affected


def resume(root, key, profile):
    """Run a stopped or failed task again: failed or blocked milestones start over from the current branch."""
    def change(value):
        if value["state"] not in ("stopped", "failed", "approved", "running") or not value["plan"]["approved"]:
            raise FridayError("invalid_state", "Task %s has nothing to resume (%s)." % (key, value["state"]))
        for item in value["milestones"]:
            if item["state"] in ("failed", "blocked", "running"):
                item["state"] = "pending"
        value["state"] = "stopped"
    cards.update(root, key, change)
    cards.record(root, key, "resumed")
    return run_task(root, key, profile)
