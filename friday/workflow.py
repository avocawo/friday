"""Validate a staged plan and dispatch bounded briefs to configured model adapters."""
import contextlib
import json
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from . import crew, gate, isolated_preview, isolation, profile, runners, schemas, state, worktree as worktrees
from .errors import FridayError
from .locks import locked


def load_plan(root, key, document, replace=False, expected_decision_revision=None, planning=None):
    try:
        plan = json.loads(Path(document).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FridayError("invalid_plan", str(exc))
    items = plan.get("stages") if isinstance(plan, dict) else None
    if not isinstance(items, list) or not items:
        raise FridayError("invalid_plan", "Plan requires a non-empty stages array.")
    stages = {}
    proposed_crews = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise FridayError("invalid_plan", "Every stage requires an id.")
        name = state.validate_key(item["id"])
        if name in stages:
            raise FridayError("invalid_plan", "Duplicate stage: " + name)
        if not isinstance(item.get("brief"), str) or not item["brief"].strip():
            raise FridayError("invalid_plan", "Stage requires a brief: " + name)
        criteria, dependencies = item.get("criteria"), item.get("depends_on", [])
        if not isinstance(criteria, list) or not criteria or any(not isinstance(x, str) or not x.strip() for x in criteria):
            raise FridayError("invalid_plan", "Stage requires acceptance criteria: " + name)
        if not isinstance(dependencies, list) or any(not isinstance(x, str) for x in dependencies):
            raise FridayError("invalid_plan", "depends_on must be a list of stage ids.")
        files = item.get("files", [])
        if not isinstance(files, list) or len(files) > 10 or any(not isinstance(x, str) or not x or x.startswith("/") or ".." in Path(x).parts for x in files):
            raise FridayError("invalid_plan", "Stage files must be at most 10 relative paths.")
        added = item.get("max_added_lines", 500)
        if not isinstance(added, int) or isinstance(added, bool) or not 0 <= added <= 500:
            raise FridayError("invalid_plan", "Stage added-line estimate must be between 0 and 500.")
        stages[name] = {"status": "open", "brief": item["brief"], "criteria": criteria, "depends_on": dependencies}
        if files:
            stages[name]["files"] = files
        if "crew" in item:
            proposed_crews[name] = item["crew"]
    visited, visiting = set(), set()
    def visit(name):
        if name in visiting:
            raise FridayError("invalid_plan", "Cyclic stage dependency: " + name)
        if name not in stages:
            raise FridayError("invalid_plan", "Unknown stage dependency: " + name)
        if name in visited:
            return
        visiting.add(name)
        for dep in stages[name]["depends_on"]:
            visit(dep)
        visiting.remove(name)
        visited.add(name)
    for name in stages:
        visit(name)
    def change(card):
        if expected_decision_revision is not None and card.get("decision_revision", 0) != expected_decision_revision:
            raise FridayError("stale_evidence", "A decision changed while planning.")
        if card["stages"] and not replace:
            raise FridayError("plan_exists", "The task already has stages; replacing them would lose evidence.")
        selected = profile.load(card["profile"])
        for name, proposed in proposed_crews.items():
            stages[name]["crew"] = crew.validate(proposed, selected)
            stages[name]["crew_binding"] = crew.catalog_fingerprint(stages[name]["crew"], selected)
        worktree = card.get("worktree", selected["repository"])
        base = card.get("plan_base") or gate.git(worktree, "rev-parse", "HEAD").decode().strip()
        gate.git(worktree, "merge-base", "--is-ancestor", base, "HEAD")
        if card["stages"]:
            card["events"].append({"type": "plan_replaced", "previous_stages": card["stages"], "at": state.now()})
        card["plan_revision"] = card.get("plan_revision", 0) + 1
        card["plan_policy"] = profile.policy_fingerprint(selected)
        card["plan_base"] = base
        card["plan_approved"] = card.get("mode") == "auto"
        card["stages"] = stages
        if planning is not None:
            card["planning_questions"] = planning["questions"]
            card["planning_assumptions"] = planning.get("assumptions", [])
            card["suggested_issues"] = planning["suggested_issues"]
            card["events"].append({"type": "planning", "model": planning["model"],
                                   "evidence": planning["evidence"], "at": state.now()})
        elif replace:
            card["planning_questions"] = []
            card["planning_assumptions"] = []
            card["suggested_issues"] = []
        card["events"].append({"type": "plan_loaded", "at": state.now()})
    return state.update(root, key, change)


def unanswered(card):
    prefix = "plan-" + str(card.get("plan_revision", 0)) + "-question-"
    return [{"name": prefix + str(index + 1), "question": question}
            for index, question in enumerate(card.get("planning_questions", []))
            if prefix + str(index + 1) not in card.get("decisions", {})]


def approve_plan(root, key):
    def change(card):
        if not card.get("plan_base") or not card["stages"]:
            raise FridayError("invalid_plan", "Load a plan before approving it.")
        card["plan_approved"] = True
        card["events"].append({"type": "plan_approved", "at": state.now()})
    return state.update(root, key, change)


def ready(card):
    return [name for name, stage in card["stages"].items() if stage.get("status") == "open" and
            all(card["stages"].get(dep, {}).get("status") == "closed" for dep in stage.get("depends_on", []))]


def review_diff(worktree, card, stage):
    base = stage.get("review_base", card.get("plan_base"))
    if not isinstance(base, str) or not re.fullmatch(r"[a-f0-9]{40,64}", base):
        raise FridayError("invalid_plan", "Review requires a valid base commit.")
    gate.git(worktree, "merge-base", "--is-ancestor", base, "HEAD")
    return gate.git(worktree, "diff", "--binary", base, "HEAD").decode("utf-8", "replace")


def invoke(root, key, name, role, feedback=None, require_owned=False):
    if role not in ("develop", "review", "blind-code", "blind-behavior"):
        raise FridayError("invalid_role", "Unknown model role: " + role)
    initial = state.read(root, key)
    initial_profile = profile.load(initial["profile"])
    initial_worktree = initial.get("worktree", initial_profile["repository"])
    isolation.require_runner(initial_profile, initial_worktree)
    if role == "blind-behavior":
        isolation.require_browser(initial_profile)
    browser_lock = locked(root, "resource:browser:global", timeout=1800) if role == "blind-behavior" else contextlib.nullcontext()
    with locked(root, "worker:" + state.validate_key(key) + ":" + state.validate_key(name)), \
         locked(root, "resource:branch:" + initial_worktree, timeout=1800), browser_lock:
        card = state.read(root, key)
        if name not in ready(card):
            raise FridayError("stage_not_ready", "Stage is closed or its dependencies are not closed.")
        if role == "develop" and not card.get("plan_approved"):
            raise FridayError("plan_approval_required", "Approve the plan before development.")
        selected = profile.load(card["profile"])
        profile.verify_policy(selected, card)
        if card.get("worktree", selected["repository"]) != initial_worktree:
            raise FridayError("worktree_conflict", "The worktree changed before the runner started.")
        definitions = selected.get("runners", {})
        runner = definitions.get(role) if isinstance(definitions, dict) else None
        if not isinstance(runner, dict) or not isinstance(runner.get("model"), str):
            raise FridayError("invalid_profile", "Profile requires a model runner for " + role)
        command = runners.command(runner)
        developer = definitions.get("develop", {})
        if role != "develop":
            if not isinstance(developer, dict) or not isinstance(developer.get("model"), str) or not developer["model"]:
                raise FridayError("invalid_profile", "The developer model must be configured before review.")
            if runner["model"] == developer["model"]:
                raise FridayError("reviewer_not_independent", "Reviewer must use a different configured model.")
            previous = next((event for event in reversed(card["events"]) if event.get("type") == "model_run" and
                             event.get("stage") == name and event.get("role") == "develop"), None)
            if previous and runner["model"] == previous.get("model"):
                raise FridayError("reviewer_not_independent", "Reviewer must differ from the model that developed this stage.")
        stage = card["stages"][name]
        worktree = card.get("worktree", selected["repository"])
        if require_owned:
            if not card.get("worktree") or not card.get("branch"):
                raise FridayError("worktree_required", "The runner requires a Friday worktree.")
            worktrees.verify(selected["repository"], worktree, card["branch"])
        if role != "develop" and gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
            raise FridayError("dirty_tree", "Commit changes before running a review.")
        head = gate.git(worktree, "rev-parse", "HEAD").decode().strip()
        request = {"role": role, "criteria": stage["criteria"], "model": runner["model"]}
        if runner.get("vendor"):
            request["vendor"] = runner["vendor"]
        if role == "blind-behavior" and "behavior_test_policy" in selected:
            if not isinstance(selected["behavior_test_policy"], str):
                raise FridayError("invalid_profile", "behavior_test_policy must be a string.")
            request["behavior_test_policy"] = selected["behavior_test_policy"]
        if role == "blind-behavior" and card.get("preview_url"):
            request["preview_url"] = card["preview_url"]
        if role in ("develop", "review"):
            request.update({"task": key, "stage": name, "brief": stage["brief"], "worktree": worktree,
                            "decisions": card.get("decisions", {}), "planning_assumptions": card.get("planning_assumptions", []),
                            "planning_questions": unanswered(card)})
            if selected.get("engineering_policy"):
                request["engineering_policy"] = selected["engineering_policy"]
        if role == "develop" and feedback:
            request["feedback"] = feedback
        if role in ("review", "blind-code"):
            if gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
                raise FridayError("dirty_tree", "Commit changes before reviewing the diff.")
            request["diff"] = review_diff(worktree, card, stage)
        with tempfile.TemporaryDirectory(prefix="friday-blind-") as isolated, contextlib.ExitStack() as stack:
            cwd = isolated if role in ("blind-code", "blind-behavior") else worktree
            environment = runners.environment(runner)
            if role == "blind-behavior":
                request["preview_url"] = stack.enter_context(isolated_preview.open_preview(selected, worktree))
                environment["FRIDAY_BROWSER_CONFIG"] = json.dumps(selected.get("browser", {}))
            try:
                result = subprocess.run(command, cwd=cwd, input=json.dumps(request, ensure_ascii=False), text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800, env=environment)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise FridayError("runner_unavailable", "Runner could not finish: " + type(exc).__name__)
        if result.returncode:
            raise FridayError("runner_failed", "Model runner exited %s." % result.returncode)
        if role in ("develop", "review") and profile.load(card["profile"]).get("engineering_policy", []) != selected.get("engineering_policy", []):
            raise FridayError("stale_evidence", "Project engineering policy changed while the agent was running.")
        if role != "develop" and (gate.git(worktree, "rev-parse", "HEAD").decode().strip() != head or
                                  gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all")):
            raise FridayError("stale_evidence", "The worktree changed while the review was running.")
        snapshot = None
        if role == "develop":
            if gate.git(worktree, "rev-parse", "HEAD").decode().strip() != head:
                raise FridayError("stale_evidence", "The developer changed HEAD; Friday cannot safely commit the result.")
            snapshot = worktrees.fingerprint(worktree)
        verdict = None
        if role != "develop":
            try:
                answer = json.loads(result.stdout)
                verdict = schemas.validate_review(answer, observed=role == "blind-behavior")
            except ValueError as exc:
                raise FridayError("invalid_verdict", str(exc))
        evidence = state.task_path(root, key).parent / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        path = evidence / (uuid.uuid4().hex + ".txt")
        path.write_text(result.stdout, encoding="utf-8")
        def record(latest):
            if latest["stages"].get(name, {}).get("status") != "open":
                raise FridayError("invalid_stage", "Stage changed while the runner was working.")
            if latest.get("decision_revision", 0) != card.get("decision_revision", 0):
                raise FridayError("stale_evidence", "A decision changed while the runner was working.")
            if latest.get("plan_revision", 0) != card.get("plan_revision", 0):
                raise FridayError("stale_evidence", "The plan changed while the runner was working.")
            event = {"type": "model_run", "stage": name, "role": role, "model": runner["model"],
                     "evidence": str(path), "at": state.now(), "head": head, "verdict": verdict,
                     "worktree_snapshot": snapshot, "decision_revision": card.get("decision_revision", 0),
                     "plan_revision": card.get("plan_revision", 0)}
            latest["events"].append(event)
            return event
        return state.update(root, key, record)
