"""Drive configured stage runners in a Friday-owned worktree, without public actions."""
import hashlib
import os
import subprocess
from pathlib import Path

from . import browser_auth, dispatch, gate, isolation, journal, planner, profile, schemas, stages, state, workflow, worktree as worktrees
from .errors import FridayError
from .locks import locked

ROLES = ("review", "blind-code", "blind-behavior")


def git(worktree, *args):
    return gate.git(worktree, *args).decode("utf-8", "replace").strip()


def set_progress(root, key, name, **changes):
    def update(card):
        card["stages"][name].setdefault("automation", {}).update(changes)
    state.update(root, key, update)


def current(root, key, name):
    card = state.read(root, key)
    selected = profile.load(card["profile"])
    worktree = card.get("worktree")
    if not worktree or not card.get("branch"):
        raise FridayError("worktree_required", "Autopilot requires a worktree created by Friday.")
    if git(worktree, "rev-parse", "--show-toplevel") != str(Path(worktree).resolve()):
        raise FridayError("worktree_conflict", "The worktree path does not match the Git root.")
    if git(worktree, "branch", "--show-current") != card["branch"]:
        raise FridayError("worktree_conflict", "The worktree branch has changed.")
    worktrees.verify(selected["repository"], worktree, card["branch"])
    stage = card["stages"].get(name)
    if not stage or "criteria" not in stage:
        raise FridayError("invalid_stage", "Autopilot requires a stage loaded from a plan.")
    return card, selected, worktree, stage


def preflight(selected, worktree, require_runners=True):
    image, command = selected.get("gate_image"), selected.get("gate")
    if not isinstance(image, str) or not image or not isinstance(command, list) or not command:
        raise FridayError("invalid_profile", "Autopilot requires a configured isolated gate and image.")
    if require_runners:
        runners = selected.get("runners")
        if not isinstance(runners, dict):
            raise FridayError("invalid_profile", "Autopilot requires all model runners.")
        for role in ("develop",) + ROLES:
            runner = runners.get(role)
            if not isinstance(runner, dict) or not isinstance(runner.get("model"), str) or not runner["model"]:
                raise FridayError("invalid_profile", "Autopilot requires a model for " + role)
            arguments = runner.get("command")
            if not isinstance(arguments, list) or not arguments or any(not isinstance(arg, str) or not arg for arg in arguments):
                raise FridayError("invalid_profile", "Autopilot requires a runner command for " + role)
            if role != "develop" and runner["model"] == runners["develop"]["model"]:
                raise FridayError("reviewer_not_independent", "Reviewer must use a different configured model.")
    image = gate.image_id(image)
    lockfile = selected.get("gate_lockfile")
    if lockfile:
        path = Path(lockfile)
        if path.is_absolute() or ".." in path.parts:
            raise FridayError("invalid_profile", "gate_lockfile must be a relative path without '..'.")
        try:
            local = hashlib.sha256((Path(worktree) / path).read_bytes()).hexdigest()
            result = subprocess.run(["docker", "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                                     "--entrypoint", "sha256sum", image, "/app/" + lockfile],
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FridayError("environment_unavailable", "Cannot check gate image lockfile: " + type(exc).__name__)
        if result.returncode or not result.stdout.split() or result.stdout.split()[0] != local:
            raise FridayError("environment_unavailable", "Gate image lockfile differs from the worktree.")


def check_preflight(root, key, name, selected, worktree, require_runners=True):
    try:
        preflight(selected, worktree, require_runners=require_runners)
    except FridayError as exc:
        if exc.code == "environment_unavailable":
            def record(card):
                stage = card["stages"][name]
                stage["gate"] = {"status": "environment_unavailable", "message": str(exc), "at": state.now()}
                stage["environment_lock"] = True
            state.update(root, key, record)
        raise


def commit(root, worktree, key, name):
    gate.git(worktree, "add", "-A")
    tree = git(worktree, "write-tree")
    set_progress(root, key, name, commit_tree=tree)
    if gate.git(worktree, "diff", "--cached", "--name-only"):
        gate.git(worktree, "commit", "-m", "%s: %s" % (key, name))
    return git(worktree, "rev-parse", "HEAD")


def check_size(worktree, stage):
    raw = gate.git(worktree, "diff", "--no-renames", "--numstat", "-z", "HEAD")
    counts = {}
    for entry in filter(None, raw.split(b"\0")):
        added, _, filename = entry.split(b"\t", 2)
        if added == b"-":
            raise FridayError("stage_too_large", "Binary changes require a separately reviewed stage.")
        counts[os.fsdecode(filename)] = int(added)
    untracked = gate.git(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    for name in filter(None, untracked.split(b"\0")):
        path = Path(worktree) / os.fsdecode(name)
        if path.is_symlink() or not path.is_file():
            raise FridayError("stage_too_large", "Unsupported new file: " + os.fsdecode(name))
        data = path.read_bytes()
        if b"\0" in data:
            raise FridayError("stage_too_large", "Binary changes require a separately reviewed stage.")
        counts[os.fsdecode(name)] = len(data.splitlines())
    if len(counts) > 10 or sum(counts.values()) > 500:
        raise FridayError("stage_too_large", "Stage exceeds 10 files or 500 added lines.")
    if stage.get("files") and not set(counts).issubset(stage["files"]):
        raise FridayError("stage_scope", "The developer changed files outside the stage plan.")


def run_stage(root, key, name):
    with locked(root, "autopilot:" + state.validate_key(key) + ":" + state.validate_key(name), timeout=1800):
        card, selected, worktree, stage = current(root, key, name)
        if name not in workflow.ready(card):
            raise FridayError("stage_not_ready", "Stage is closed or its dependencies are not closed.")
        progress = stage.get("automation", {})
        if not progress:
            if gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
                raise FridayError("dirty_tree", "Autopilot requires a clean worktree at the start.")
            set_progress(root, key, name, phase="develop", attempt=1, head=git(worktree, "rev-parse", "HEAD"))
        while True:
            card, selected, worktree, stage = current(root, key, name)
            progress = stage.get("automation")
            if not progress:
                raise FridayError("stale_evidence", "A task decision changed while automation was running.")
            phase = progress["phase"]
            if phase != "commit":
                if gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
                    raise FridayError("dirty_tree", "The worktree changed outside the completed model run.")
                if git(worktree, "rev-parse", "HEAD") != progress["head"]:
                    raise FridayError("stale_evidence", "The worktree HEAD changed outside the automation.")
            if phase == "develop":
                check_preflight(root, key, name, selected, worktree)
                event = workflow.invoke(root, key, name, "develop", progress.get("feedback"), require_owned=True)
                set_progress(root, key, name, phase="commit", snapshot=event["worktree_snapshot"])
            elif phase == "commit":
                with locked(root, "resource:branch:" + worktree, timeout=1800):
                    current(root, key, name)
                    head = git(worktree, "rev-parse", "HEAD")
                    if head != progress["head"]:
                        parents = git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()
                        tree = git(worktree, "rev-parse", "HEAD^{tree}")
                        dirty = gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all")
                        if len(parents) != 2 or parents[1] != progress["head"] or tree != progress.get("commit_tree") or dirty:
                            raise FridayError("stale_evidence", "HEAD changed outside the interrupted commit.")
                    else:
                        if worktrees.fingerprint(worktree) != progress.get("snapshot"):
                            raise FridayError("stale_evidence", "The completed developer result changed before commit.")
                        check_size(worktree, stage)
                        check_preflight(root, key, name, selected, worktree)
                        head = commit(root, worktree, key, name)
                set_progress(root, key, name, phase="review", head=head, feedback=None)
            elif phase in ROLES:
                check_preflight(root, key, name, selected, worktree)
                event = workflow.invoke(root, key, name, phase, require_owned=True)
                if event["verdict"] == "pass":
                    next_phase = ROLES[ROLES.index(phase) + 1] if phase != ROLES[-1] else "gate"
                    set_progress(root, key, name, phase=next_phase)
                else:
                    if phase == "blind-behavior" and schemas.environment_failure(event["evidence"]):
                        raise FridayError("environment_unavailable", "Browser verification is unavailable; the current review can be retried.")
                    if progress["attempt"] >= 3:
                        raise FridayError("review_failed", "Review still fails after three attempts: " + phase)
                    feedback = Path(event["evidence"]).read_text(encoding="utf-8")[-10000:]
                    set_progress(root, key, name, phase="develop", attempt=progress["attempt"] + 1, feedback=feedback)
            elif phase == "gate":
                selected["repository"] = worktree
                result = gate.execute(root, key, name, selected)
                if result["status"] != "passed":
                    if result["status"] == "environment_unavailable" or progress["attempt"] >= 3:
                        raise FridayError(result["status"], "Gate did not pass; see " + result["log"])
                    feedback = Path(result["log"]).read_text(encoding="utf-8")[-10000:]
                    set_progress(root, key, name, phase="develop", attempt=progress["attempt"] + 1, feedback=feedback)
                else:
                    stages.close(root, key, name, "gate")
                    journal.sync(root, key)
                    return {"stage": name, "status": "closed", "head": result["head"]}
            else:
                raise FridayError("invalid_state", "Unknown automation phase: " + str(phase))


def run(root, key):
    initial = state.read(root, key)
    selected = profile.load(initial["profile"])
    isolation.require_runner(selected, initial.get("worktree", selected["repository"]))
    isolation.require_browser(selected)
    if isinstance(selected.get("browser"), dict):
        browser_auth.capture(selected["browser"])
    completed = []
    while True:
        card = state.read(root, key)
        if not card["stages"]:
            planner.generate(root, key)
            card = state.read(root, key)
        if not card.get("plan_approved"):
            raise FridayError("plan_approval_required", "Approve the plan before starting autopilot.")
        ready = workflow.ready(card)
        if not ready:
            return {"completed": completed, "remaining": [name for name, stage in card["stages"].items()
                                                           if stage.get("status") != "closed"]}
        name = ready[0]
        if card["stages"][name].get("crew"):
            roles = {item["role"] for item in card["stages"][name]["crew"]}
            if not set(ROLES).issubset(roles):
                raise FridayError("incomplete_team", "A team must include review, blind-code and blind-behavior before automation.")
            if not card.get("worktree"):
                raise FridayError("worktree_required", "Team automation requires a Friday worktree.")
            selected = profile.load(card["profile"])
            check_preflight(root, key, name, selected, card["worktree"], require_runners=False)
            team = dispatch.run(root, key, name)
            selected["repository"] = card["worktree"]
            result = gate.execute(root, key, name, selected)
            if result["status"] != "passed":
                raise FridayError(result["status"], "Gate did not pass; see " + result["log"])
            stages.close(root, key, name, "gate")
            journal.sync(root, key)
            completed.append({"stage": name, "status": "closed", "head": team["head"]})
        else:
            completed.append(run_stage(root, key, name))
