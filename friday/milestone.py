"""Run a task's milestones: one executor session per milestone, gate, review by a stronger model, disputes to the owner."""
import json
import os
import signal
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import card as cards
from . import final, gate, models, runner, workspace
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
    "change git configuration, do not delete or rewrite earlier commits. Write summary in English.")
REVIEW_RULES = (
    "You review one milestone of an approved plan. You get the milestone's goal, criteria and the project rules digest, "
    "the diff of this milestone only, and the result of the isolated gate (lint, typecheck, tests) on the same commit. "
    "You may read the repository for context. Judge every criterion as pass, fail or unverifiable with concrete evidence "
    "(file and line). Report every actionable defect in one pass: blocking for wrong behaviour, broken criteria, breaches "
    "of project rules or missing tests; minor for anything that can wait. Do not report style the linter accepts. "
    "verdict is pass when no blocking finding remains. Do not edit files. Write problems and evidence in English.")


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
    raise FridayError("not_found", "Unknown milestone: {milestone_id}", milestone_id=milestone_id)


def set_state(root, key, milestone_id, **changes):
    def change(value):
        for item in value["milestones"]:
            if item["id"] == milestone_id:
                item.update(changes)
                return
        raise FridayError("not_found", "Unknown milestone: {milestone_id}", milestone_id=milestone_id)
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


def run_role(root, key, profile, role, entry, worktree, prompt, schema, milestone_id, *, write=False, resume=None, mcp=None):
    commands = list(profile.get("executor_commands", [])) + GIT_COMMANDS if write else []
    env = dict(models.environment(entry, profile), **(identity(worktree) if write else {}))
    if mcp:
        env["MCP_TIMEOUT"] = "180000"  # the preview browser takes a while to start
    spec = runner.Spec(driver=entry["driver"], model=entry["model"], effort=entry.get("effort"), prompt=prompt, cwd=worktree,
                       log_dir="", write=write, commands=commands, schema=schema, resume=resume,
                       env=env, deny_read=profile.get("executor_deny_read", []), mcp=mcp)
    return cards.run_agent(root, key, spec, role=role, milestone=milestone_id, phase=role, entry=entry, profile=profile)


def run(root, key, profile, milestone_id, worktree=None):
    """Drive one milestone to done. Returns its final record; raises only when the work cannot pass the gate."""
    card = cards.read(root, key)
    worktree, layout = worktree or card["worktree"], card["cast"]
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
                   milestone_text(key, document, item, cards.for_agents(card["decisions"])))
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
            cards.record(root, key, "cast_note", milestone=milestone_id, note=note or "executor quota")
            prompt, session = base_prompt + ("\n\nPrevious attempt feedback:\n" + feedback_text if round_number > 1 else ""), None
            continue
        if result.status != "ok":
            raise FridayError("agent_failed", "Executor failed on {milestone_id} ({status}): {error}", milestone_id=milestone_id, status=result.status, error=result.error)
        session = result.session
        answer = result.structured or {}
        if answer.get("summary"):
            set_state(root, key, milestone_id, summary=answer["summary"], executor=entry["model"])
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
                gate_record = gate.check(root, profile, worktree, card.get("base"), log,
                                         on_wait=lambda name: cards.waiting(root, key, name, milestone_id))
                cards.stop_waiting(root, key, milestone_id)
                cards.record(root, key, "gate", milestone=milestone_id, status=gate_record["status"], head=gate_record["head"])
                if gate_record["status"] == "environment_unavailable":
                    set_state(root, key, milestone_id, state="blocked")
                    raise FridayError("environment_unavailable", "The gate environment is unavailable; see {log}", log=gate_record["log"])
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
                    raise FridayError("agent_failed", "No reviewer with quota is available for {milestone_id}", milestone_id=milestone_id)
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
        reason = "no commits" if git(worktree, "rev-parse", "HEAD").strip() == start else "the gate never passed"
        set_state(root, key, milestone_id, state="failed")
        cards.record(root, key, "milestone_failed", milestone=milestone_id, reason=reason)
        raise FridayError("milestone_failed", "Milestone {milestone_id} was never reviewed: {reason}.", milestone_id=milestone_id, reason=reason)
    if gate_record is None or gate_record["status"] == "passed":
        # The pipeline does not stop on review disagreement: what remains goes to the owner with the draft.
        for finding in findings:
            disputes.append(dict(finding, milestone=milestone_id, executor_reason="Not fixed within the allowed rounds.",
                                 executor_evidence="", reviewer=review_entry["model"], status="owner"))
        return close(root, key, milestone_id, item, start, worktree, review_value, gate_record, disputes, extra, ROUNDS)
    set_state(root, key, milestone_id, state="failed")
    cards.record(root, key, "milestone_failed", milestone=milestone_id, gate=gate_record["log"])
    raise FridayError("milestone_failed", "Milestone {milestone_id} still fails the gate after all rounds; see {log}", milestone_id=milestone_id, log=gate_record["log"])


def accept_in_browser(root, key, profile, layout, author, worktree, item):
    """Blocking findings from the browser acceptance of a UI milestone; an unverifiable check is noted, not blocking."""
    entry, note = models.reviewer(profile, layout, "browser", author, models.availability(root))
    if note:
        cards.record(root, key, "cast_note", milestone=item["id"], note=note)
    result = final.browser(root, key, profile, entry, worktree, item, run_role, REVIEW_SCHEMA)
    if result is None:
        return []
    if result.status != "ok" or not isinstance(result.structured, dict):
        cards.record(root, key, "browser_unverifiable", milestone=item["id"], reason=result.error or result.status)
        return []
    value = result.structured
    statuses = [c["status"] for c in value["criteria_results"]]
    cards.record(root, key, "browser_checked", milestone=item["id"], verdict=value["verdict"], model=entry["model"],
                 unverifiable=statuses.count("unverifiable"))
    if not statuses or all(status == "unverifiable" for status in statuses):
        cards.record(root, key, "browser_unverifiable", milestone=item["id"], reason="no criterion could be observed")
    return [finding for finding in value["findings"] if finding["severity"] == "blocking"]


def review(root, key, profile, entry, worktree, document, item, start, gate_record, previous, disputes):
    diff = git(worktree, "diff", start + "..HEAD")
    gate_text = ("not configured" if gate_record is None else "%s on %s (command: %s)" % (
        gate_record["status"], gate_record["head"], " ".join(profile.get("gate", []))))
    prompt = (REVIEW_RULES + "\n\n" + milestone_text(key, document, item, cards.for_agents(cards.read(root, key)["decisions"])) +
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
        raise FridayError("agent_failed", "Review of {id} failed ({status}): {error}", id=item["id"], status=result.status, error=result.error)
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
        raise FridayError("invalid_state", "Approve the plan of {key} before running it.", key=key)
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
            group = [name for name in pending if define(document, name)["parallel"]]
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
    cards.update(root, key, lambda value: value.update(state="done" if not left else "stopped"))
    cards.record(root, key, "run_task_finished", completed=[item["milestone"] for item in completed], left=left)
    return {"completed": completed, "left": left, "delivery": delivery}


FIX_RULES = (
    "The final review of the whole task found blocking problems. Fix each one in this repository, run your checks and "
    "commit with git commit -m \"{key} final: <short summary>\", or dispute a finding in disputes with its number, a reason "
    "and evidence. Disputed findings go to the owner. Follow the project rules digest.")


def finish(root, key, profile):
    """Final review and blind check, one fix round for blocking findings, then the draft merge request."""
    card = cards.read(root, key)
    worktree, layout = card["worktree"], card["cast"]
    cards.record(root, key, "final_started")
    results, final_entry = final.final_review(root, key, profile, run_role, REVIEW_SCHEMA)
    blocking = [f for value in results.values() for f in value["findings"] if f["severity"] == "blocking"]
    minor = [f for value in results.values() for f in value["findings"] if f["severity"] == "minor"]
    cards.update(root, key, lambda value: value.update(final_minor=minor))
    if blocking:
        document = load_plan(root, key)
        entry = layout["develop"]
        allowed = list(profile.get("executor_commands", [])) + GIT_COMMANDS
        prompt = (FIX_RULES.format(key=key) + "\n\nShell commands you may run, each starting exactly like one of these:\n" +
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
        if profile.get("gate"):
            log = cards.directory(root, key) / "gates" / "final.log"
            record = gate.check(root, profile, worktree, card.get("base"), log,
                                on_wait=lambda name: cards.waiting(root, key, name, "final"))
            cards.stop_waiting(root, key, "final")
            cards.record(root, key, "gate", milestone="final", status=record["status"], head=record["head"])
            if record["status"] != "passed":
                raise FridayError("final_failed", "The gate fails after the final fixes; see {log}", log=record["log"])
        again, _ = final.final_review(root, key, profile, run_role, REVIEW_SCHEMA, roles=("final-review",))
        disputed = {(d["file"], d["problem"]) for d in disputes}
        remaining = [f for f in again["final-review"]["findings"] if f["severity"] == "blocking" and (f["file"], f["problem"]) not in disputed]
        disputes += [dict(f, milestone="final", executor_reason="Not fixed after the final review.", executor_evidence="",
                          reviewer=final_entry["model"], status="owner") for f in remaining]
        if disputes:
            cards.update(root, key, lambda value: value["disputes"].extend(disputes))
    return final.deliver(root, key, profile)


def run_parallel(root, key, profile, group):
    """Milestones the plan marks parallel (no shared files) run at once, each in its own worktree; their commits
    are then cherry-picked onto the task branch in plan order."""
    card = cards.read(root, key)
    main, branch, repo = card["worktree"], card["branch"], profile["repository"]
    lanes = {}
    for name in group:
        path, lane = str(Path(main).parent / (Path(main).name + "--" + name)), branch + "--" + name
        workspace.git(repo, "worktree", "add", "-q", "-b", lane, path, git(main, "rev-parse", "HEAD").strip())
        lanes[name] = (path, lane)
    cards.record(root, key, "parallel_started", milestones=group)
    results, errors = {}, {}
    pool = ThreadPoolExecutor(max_workers=len(group))
    futures = {name: pool.submit(run, root, key, profile, name, lanes[name][0]) for name in group}
    try:
        for name, future in futures.items():
            try:
                results[name] = future.result()
            except FridayError as exc:
                errors[name] = exc
    except BaseException:
        runner.CANCEL.set()  # Ctrl-C or SIGTERM: stop the other lanes' agents too
        pool.shutdown(wait=True)
        raise
    pool.shutdown(wait=True)
    try:
        for name in group:
            if name in results:
                integrate(root, key, profile, name, main, lanes[name][0])
    finally:
        for path, lane in lanes.values():
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
            raise FridayError("merge_conflict", "Parallel milestone {name} conflicts with the task branch: {value}", name=name, value=result.stderr.strip())
    subjects = [line.split(" ", 1) for line in git(main, "log", "--format=%H %s", before + "..HEAD").splitlines()]
    stages = [{"id": stage["id"], "commit": next((sha for sha, subject in subjects if (" " + stage["id"] + ":") in (" " + subject)), None)}
              for stage in item["stages"]]
    set_state(root, key, name, stages=stages, head=git(main, "rev-parse", "HEAD").strip(), start=before)
    cards.record(root, key, "parallel_integrated", milestone=name, commits=len(commits))


def decide(root, key, name, text, reopen=(), profile=None):
    """Record an owner decision; only the milestones the owner names (and those after them) are reopened.
    The decision is kept as written; agents get its English translation."""
    from . import translate
    english = translate.to_english(root, key, profile, text) if profile is not None else text
    def change(value):
        ids = {item["id"] for item in value["milestones"]}
        unknown = set(reopen) - ids
        if unknown:
            raise FridayError("not_found", "Unknown milestones: {items}", items=", ".join(sorted(unknown)))
        value["decisions"].append(dict({"name": name, "text": text, "reopen": list(reopen)},
                                       **({"text_en": english} if english != text else {})))
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


def resume(root, key, profile):
    """Run a stopped or failed task again: failed or blocked milestones start over from the current branch."""
    def change(value):
        if value["state"] not in ("stopped", "failed", "approved", "running") or not value["plan"]["approved"]:
            raise FridayError("invalid_state", "Task {key} has nothing to resume ({state}).", key=key, state=value["state"])
        for item in value["milestones"]:
            if item["state"] in ("failed", "blocked", "running"):
                item["state"] = "pending"
        value["state"] = "stopped"
    cards.update(root, key, change)
    cards.record(root, key, "resumed")
    return run_task(root, key, profile)
