"""Ask the configured read-only planner for a bounded, reviewable plan."""
import json
import subprocess
import tempfile
from pathlib import Path

from . import isolation, journal, profile, runners, state, workflow, worktree
from .errors import FridayError
from .locks import locked


def generate(root, key):
    initial = state.read(root, key)
    selected = profile.load(initial["profile"])
    target = initial.get("worktree")
    branch = initial.get("branch")
    if not target or not branch:
        raise FridayError("worktree_required", "Planning requires a worktree created by Friday.")
    isolation.require_runner(selected, target)
    definitions = selected.get("runners", {})
    runner = definitions.get("plan") if isinstance(definitions, dict) else None
    if not isinstance(runner, dict) or not isinstance(runner.get("model"), str) or not runner["model"]:
        raise FridayError("invalid_profile", "Configure a planner model and command.")
    command = runners.command(runner)
    with locked(root, "resource:branch:" + target, timeout=1800):
        card = state.read(root, key)
        if card["stages"] or card.get("worktree") != target:
            raise FridayError("plan_exists", "Task already has a plan or its worktree changed.")
        worktree.verify(selected["repository"], target, branch)
        ticket = card.get("ticket", {})
        request = {"role": "plan", "model": runner["model"], "task": key, "ticket": ticket,
                   "decisions": card.get("decisions", {}), "worktree": target}
        if selected.get("engineering_policy"):
            request["engineering_policy"] = selected["engineering_policy"]
        if runner.get("vendor"):
            request["vendor"] = runner["vendor"]
        agents = selected.get("agents", {})
        if not isinstance(agents, dict) or any(not isinstance(definition, dict) for definition in agents.values()):
            raise FridayError("invalid_profile", "agents must be an object of agent definitions.")
        request["available_agents"] = {name: {key: value for key, value in definition.items() if key in ("vendor", "model", "roles", "description")}
                                       for name, definition in agents.items()}
        skills = selected.get("skills", {})
        if not isinstance(skills, dict) or any(not isinstance(definition, dict) for definition in skills.values()):
            raise FridayError("invalid_profile", "skills must be an object of skill definitions.")
        request["available_skills"] = {name: {key: value for key, value in definition.items() if key == "description"}
                                       for name, definition in skills.items()}
        request["max_agents_per_stage"] = selected.get("max_agents_per_stage", 6)
        if "figma.com/" in json.dumps(ticket, ensure_ascii=False).lower() and selected.get("dor_file"):
            try:
                request["definition_of_ready"] = Path(selected["dor_file"]).read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise FridayError("invalid_profile", "Cannot read Definition of Ready: " + str(exc))
        for attempt in range(2):
            try:
                result = subprocess.run(command, cwd=target, input=json.dumps(request, ensure_ascii=False), text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800,
                                        env=runners.environment(runner))
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise FridayError("runner_unavailable", "Planner could not finish: " + type(exc).__name__)
            if result.returncode:
                raise FridayError("runner_failed", "Planner exited %s." % result.returncode)
            try:
                generated = json.loads(result.stdout)
            except ValueError:
                raise FridayError("invalid_plan", "Planner did not return JSON.")
            if not isinstance(generated, dict) or any(not isinstance(generated.get(field, []), list) or any(not isinstance(item, str) or not item.strip() for item in generated.get(field, [])) for field in ("questions", "assumptions", "suggested_issues")):
                raise FridayError("invalid_plan", "Planner questions, assumptions and suggested issues must be arrays of non-empty strings.")
            if not generated.get("questions"):
                if attempt and not generated.get("assumptions"):
                    raise FridayError("invalid_plan", "Planner omitted the choices that resolved its questions.")
                break
            request["previous_plan"] = generated
            request["instruction"] = "Resolve every question yourself; provide concrete choices with rationale in assumptions, revise the plan, and return an empty questions array."
        else:
            raise FridayError("invalid_plan", "Planner could not resolve its own questions after a retry.")
        if profile.load(card["profile"]).get("engineering_policy", []) != selected.get("engineering_policy", []):
            raise FridayError("stale_evidence", "Project engineering policy changed while planning.")
        evidence = state.task_path(root, key).parent / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(evidence), suffix="-plan.json", delete=False) as handle:
            json.dump(generated, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            plan_path = handle.name
        metadata = {"questions": generated.get("questions", []), "assumptions": generated.get("assumptions", []), "suggested_issues": generated.get("suggested_issues", []),
                    "model": runner["model"], "evidence": plan_path}
        result = workflow.load_plan(root, key, plan_path, expected_decision_revision=card.get("decision_revision", 0), planning=metadata)
    journal.sync(root, key)
    return result
