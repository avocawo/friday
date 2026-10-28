"""Execute a validated team in dependency order with durable checkpoints."""
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from . import credentials, crew, gate, isolated_preview, isolation, profile, runners, schemas, state, worktree as worktrees
from .errors import FridayError
from .locks import locked


def definition(selected, assignments, target):
    skill_names = sorted({name for item in assignments for name in item["skills"]})
    files = {}
    for name in sorted({item["agent"] for item in assignments}):
        command = runners.command(selected["agents"][name])
        executable = shutil.which(command[0])
        if not executable:
            raise FridayError("runner_unavailable", "Agent executable is not available: " + name)
        for index, arg in enumerate([executable] + command[1:]):
            path = Path(arg)
            if index and not path.is_absolute():
                path = Path(target) / path
            if path.is_file():
                try:
                    files[name + ":" + str(index)] = {"path": str(path.absolute()),
                                                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                except OSError:
                    raise FridayError("runner_unavailable", "Cannot read agent executable or script: " + name)
    token_hashes = {name: hashlib.sha256(os.environ.get(name, "").encode()).hexdigest()
                    for name in ("FRIDAY_ZAI_TOKEN", "FRIDAY_CLAUDE_API_KEY")}
    for name in sorted({item["agent"] for item in assignments}):
        agent = selected["agents"][name]
        if agent.get("vendor") == "zai" and agent.get("token_file"):
            config = {"token_file": agent["token_file"], "token_env": "FRIDAY_ZAI_TOKEN"}
            if agent.get("token_key"):
                config["token_key"] = agent["token_key"]
            value = credentials.token(config, "FRIDAY_ZAI_TOKEN")
            token_hashes[name] = hashlib.sha256(value.encode()).hexdigest()
    safety = {}
    for module in ("autopilot.py", "browser_auth.py", "browser_probe.py", "browser_protocol.py", "browser_session.py", "codex_policy.py", "crew.py", "dispatch.py", "gate.py", "isolated_preview.py", "isolation.py", "runners.py", "schemas.py", "workflow.py"):
        path = Path(__file__).resolve().parent / module
        safety[module] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {"crew": assignments, "agents": selected.get("agents", {}), "skills": instructions(selected, skill_names),
               "engineering_policy": selected.get("engineering_policy", []),
               "browser": selected.get("browser"), "preview": selected.get("preview"),
               "gate_image": selected.get("gate_image"), "safety": safety,
               "files": files, "credentials": token_hashes}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def instructions(selected, names):
    result = []
    for name in names:
        entry = selected.get("skills", {}).get(name)
        path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path, str) or not path:
            raise FridayError("invalid_profile", "A selected skill must declare a path: " + name)
        path = Path(path).expanduser()
        if not path.is_absolute():
            path = Path(selected["path"]).parent / path
        try:
            data = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise FridayError("invalid_profile", "Cannot read selected skill: " + name)
        if len(data) > 100000:
            raise FridayError("invalid_profile", "Selected skill is too large: " + name)
        result.append({"name": name, "instructions": data})
    return result


def invoke(root, key, stage_name, item, selected, target, upstream, feedback=None):
    agent = selected["agents"][item["agent"]]
    command = runners.command(agent)
    role = item["role"]
    if role == "blind-behavior":
        isolation.require_browser(selected)
    card = state.read(root, key)
    stage = card["stages"][stage_name]
    request = {"task": key, "stage": stage_name, "worktree": target, "role": role,
               "model": agent["model"], "vendor": agent["vendor"], "brief": item["brief"],
               "criteria": stage["criteria"], "skills": instructions(selected, item["skills"]), "upstream": upstream}
    if role in ("research", "develop", "review"):
        from . import workflow
        request["planning_assumptions"] = card.get("planning_assumptions", [])
        request["planning_questions"] = workflow.unanswered(card)
        if selected.get("engineering_policy"):
            request["engineering_policy"] = selected["engineering_policy"]
    if feedback and role == "develop":
        request["feedback"] = feedback
    if role in ("blind-code", "blind-behavior"):
        for field in ("task", "stage", "worktree", "brief", "upstream"):
            request.pop(field)
    if role in ("review", "blind-code"):
        from . import workflow
        request["diff"] = workflow.review_diff(target, card, stage)
    if role == "blind-behavior":
        request["preview_url"] = card.get("preview_url")
        request["behavior_test_policy"] = selected.get("behavior_test_policy", "")
    with tempfile.TemporaryDirectory(prefix="friday-blind-") as isolated, contextlib.ExitStack() as stack:
        cwd = isolated if role in ("blind-code", "blind-behavior") else target
        environment = runners.environment(agent)
        if role == "blind-behavior":
            stack.enter_context(locked(root, "resource:browser:global", timeout=1800))
            request["preview_url"] = stack.enter_context(isolated_preview.open_preview(selected, target))
            environment["FRIDAY_BROWSER_CONFIG"] = json.dumps(selected.get("browser", {}))
        try:
            result = subprocess.run(command, cwd=cwd, input=json.dumps(request, ensure_ascii=False), text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800, env=environment)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FridayError("runner_unavailable", "Agent could not finish: " + type(exc).__name__)
    if result.returncode:
        raise FridayError("runner_failed", "Agent exited " + str(result.returncode))
    output = result.stdout
    verdict = None
    if role in ("review", "blind-code", "blind-behavior"):
        try:
            response = json.loads(output)
            verdict = schemas.validate_review(response, strict=True, observed=role == "blind-behavior")
        except ValueError as exc:
            raise FridayError("invalid_verdict", str(exc))
    if not output.strip() or len(output) > 1000000:
        raise FridayError("invalid_response", "Agent output is empty or too large.")
    directory = state.task_path(root, key).parent / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (uuid.uuid4().hex + ".txt")
    path.write_text(output, encoding="utf-8")
    return {"evidence": str(path), "verdict": verdict}


def model_event(stage_name, item, agent, head, result, revision):
    return {"type": "model_run", "stage": stage_name, "role": item["role"], "agent": item["agent"],
            "model": agent["model"], "vendor": agent["vendor"], "head": head, "verdict": result["verdict"],
            "evidence": result["evidence"], "at": state.now(),
            "decision_revision": revision[1], "plan_revision": revision[0]}


def dependents(assignments, identifier):
    invalidated = {identifier}
    while True:
        extra = {item["id"] for item in assignments if invalidated.intersection(item["depends_on"])} - invalidated
        if not extra:
            return invalidated
        invalidated.update(extra)


def run(root, key, stage_name):
    initial = state.read(root, key)
    selected = profile.load(initial["profile"])
    target, branch = initial.get("worktree"), initial.get("branch")
    if not target or not branch:
        raise FridayError("worktree_required", "A team needs a Friday worktree.")
    isolation.require_runner(selected, target)
    with locked(root, "dispatch:" + state.validate_key(key) + ":" + state.validate_key(stage_name), timeout=1800), \
         locked(root, "resource:branch:" + target, timeout=1800):
        card = state.read(root, key)
        profile.verify_policy(selected, card)
        if card.get("worktree") != target or card.get("branch") != branch:
            raise FridayError("worktree_conflict", "The task worktree changed before dispatch.")
        worktrees.verify(selected["repository"], target, branch)
        stage = card["stages"].get(stage_name)
        if not stage or stage.get("status") != "open" or not stage.get("crew"):
            raise FridayError("invalid_stage", "An open stage with a team is required.")
        if not card.get("plan_approved"):
            raise FridayError("plan_approval_required", "Approve the plan before dispatch.")
        from . import workflow
        if stage_name not in workflow.ready(card):
            raise FridayError("stage_not_ready", "Stage dependencies are incomplete.")
        assignments = crew.validate(stage["crew"], selected)
        if stage.get("crew_binding") != crew.catalog_fingerprint(assignments, selected):
            raise FridayError("stale_evidence", "The approved team catalog changed; replace the plan.")
        revision = (card.get("plan_revision", 0), card.get("decision_revision", 0))
        binding = definition(selected, assignments, target)
        head = gate.git(target, "rev-parse", "HEAD").decode().strip()
        if gate.git(target, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
            raise FridayError("dirty_tree", "Team dispatch requires a clean worktree.")
        progress = stage.get("team_progress")
        if progress and (progress.get("binding") != binding or progress.get("revision") != list(revision) or progress.get("head") != head):
            raise FridayError("stale_evidence", "Team configuration, decisions or code changed.")
        if not progress:
            def create(current):
                current["stages"][stage_name]["team_progress"] = {"binding": binding, "revision": list(revision), "head": head, "assignments": {}}
            state.update(root, key, create)
        while True:
            current = state.read(root, key)
            if (current.get("plan_revision", 0), current.get("decision_revision", 0)) != revision:
                raise FridayError("stale_evidence", "The plan or a decision changed during dispatch.")
            if definition(profile.load(current["profile"]), assignments, target) != binding:
                raise FridayError("stale_evidence", "The agent or skill configuration changed during dispatch.")
            progress = current["stages"][stage_name]["team_progress"]
            done = progress["assignments"]
            if any(item.get("status") == "running" for item in done.values()):
                raise FridayError("dispatch_interrupted", "An interrupted agent needs inspection before resuming.")
            failed = next((item for item in done.values() if item.get("status") == "failed"), None)
            if failed:
                raise FridayError("review_failed", "Agent found issues; see " + failed["evidence"])
            item = next((item for item in assignments if item["id"] not in done and
                         all(done.get(dep, {}).get("status") == "done" for dep in item["depends_on"])), None)
            if item is None:
                if len(done) != len(assignments):
                    raise FridayError("invalid_state", "No runnable agent remains.")
                return {"stage": stage_name, "assignments": done, "head": progress["head"]}
            current_head = gate.git(target, "rev-parse", "HEAD").decode().strip()
            if current_head != progress["head"] or gate.git(target, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
                raise FridayError("stale_evidence", "The worktree changed outside dispatch.")
            def begin(latest):
                latest["stages"][stage_name]["team_progress"]["assignments"][item["id"]] = {"status": "running", "head": current_head}
            state.update(root, key, begin)
            upstream = {dep: Path(done[dep]["evidence"]).read_text(encoding="utf-8") for dep in item["depends_on"]}
            result = invoke(root, key, stage_name, item, selected, target, upstream, progress.get("feedback", {}).get(item["id"]))
            if definition(profile.load(card["profile"]), assignments, target) != binding:
                raise FridayError("stale_evidence", "The agent or skill configuration changed during execution.")
            latest = state.read(root, key)
            if (latest.get("plan_revision", 0), latest.get("decision_revision", 0)) != revision:
                raise FridayError("stale_evidence", "The plan or a decision changed during agent execution.")
            new_head = gate.git(target, "rev-parse", "HEAD").decode().strip()
            if new_head != current_head:
                raise FridayError("stale_evidence", "Agent changed HEAD; its result needs inspection.")
            dirty = gate.git(target, "status", "--porcelain=v1", "-z", "--untracked-files=all")
            if item["role"] != "develop" and dirty:
                raise FridayError("stale_evidence", "Read-only agent changed the worktree.")
            if item["role"] == "develop" and dirty:
                from . import autopilot
                latest = state.read(root, key)
                if (latest.get("plan_revision", 0), latest.get("decision_revision", 0)) != revision:
                    raise FridayError("stale_evidence", "The plan or a decision changed before commit.")
                autopilot.check_size(target, stage)
                gate.git(target, "add", "-A")
                gate.git(target, "commit", "-m", key + ": " + stage_name + " / " + item["id"])
                new_head = gate.git(target, "rev-parse", "HEAD").decode().strip()
            if result["verdict"] == "fail":
                if item["role"] == "blind-behavior" and schemas.environment_failure(result["evidence"]):
                    def retry_environment(latest):
                        current = latest["stages"][stage_name]["team_progress"]
                        current["assignments"].pop(item["id"], None)
                        latest["events"].append(model_event(stage_name, item, selected["agents"][item["agent"]], current_head, result, revision))
                    state.update(root, key, retry_environment)
                    raise FridayError("environment_unavailable", "Browser verification is unavailable; the current review can be retried.")
                developers = [name for name in done if next(entry for entry in assignments if entry["id"] == name)["role"] == "develop"]
                developer = developers[-1] if developers else None
                attempts = progress.get("repair_attempts", {}).get(developer, 0) if developer else 0
                if not developer or attempts >= 2:
                    def record_failure(latest):
                        current = latest["stages"][stage_name]["team_progress"]
                        current["assignments"][item["id"]] = dict(result, status="failed", head=current_head)
                        latest["events"].append(model_event(stage_name, item, selected["agents"][item["agent"]], current_head, result, revision))
                    state.update(root, key, record_failure)
                    raise FridayError("review_failed", "Agent found issues; see " + result["evidence"])
                invalidated = dependents(assignments, developer)
                feedback = Path(result["evidence"]).read_text(encoding="utf-8")[-10000:]
                def retry(latest):
                    if (latest.get("plan_revision", 0), latest.get("decision_revision", 0)) != revision:
                        raise FridayError("stale_evidence", "The plan or a decision changed during review.")
                    current = latest["stages"][stage_name]["team_progress"]
                    latest["events"].append(model_event(stage_name, item, selected["agents"][item["agent"]], current_head, result, revision))
                    for name in invalidated:
                        current["assignments"].pop(name, None)
                    current.setdefault("feedback", {})[developer] = feedback
                    current.setdefault("repair_attempts", {})[developer] = attempts + 1
                state.update(root, key, retry)
                continue
            def finish(latest):
                if (latest.get("plan_revision", 0), latest.get("decision_revision", 0)) != revision:
                    raise FridayError("stale_evidence", "The plan or a decision changed during agent execution.")
                progress = latest["stages"][stage_name]["team_progress"]
                progress["head"] = new_head
                progress["assignments"][item["id"]] = dict(result, status="done", head=new_head)
                progress.get("feedback", {}).pop(item["id"], None)
                latest["events"].append(model_event(stage_name, item, selected["agents"][item["agent"]], new_head, result, revision))
            state.update(root, key, finish)
