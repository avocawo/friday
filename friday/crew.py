"""Validate model-proposed roles against a project's allowed agent catalog."""
import hashlib
import json

from .errors import FridayError
from .state import validate_key

ROLES = ("research", "develop", "review", "blind-code", "blind-behavior")


def catalog_fingerprint(assignments, selected):
    agent_names = sorted({item["agent"] for item in assignments})
    skill_names = sorted({name for item in assignments for name in item["skills"]})
    payload = {"agents": {name: selected["agents"][name] for name in agent_names},
               "skills": {name: selected.get("skills", {})[name] for name in skill_names}}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate(proposed, selected):
    catalog = selected.get("agents", {})
    skills = selected.get("skills", {})
    limit = selected.get("max_agents_per_stage", 6)
    if not isinstance(catalog, dict) or not isinstance(skills, dict):
        raise FridayError("invalid_profile", "agents and skills must be objects.")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 16:
        raise FridayError("invalid_profile", "max_agents_per_stage must be between 1 and 16.")
    if not isinstance(proposed, list) or len(proposed) > limit:
        raise FridayError("invalid_plan", "Agent assignments exceed the configured limit.")
    assignments, vendors = {}, {}
    for item in proposed:
        if not isinstance(item, dict) or set(item) != {"id", "agent", "role", "brief", "skills", "depends_on"}:
            raise FridayError("invalid_plan", "An agent assignment must have id, agent, role, brief, skills and depends_on.")
        name = validate_key(item["id"]) if isinstance(item["id"], str) else None
        if not name or name in assignments:
            raise FridayError("invalid_plan", "Agent assignment IDs must be unique.")
        agent, role = item["agent"], item["role"]
        if not isinstance(agent, str) or agent not in catalog or role not in ROLES:
            raise FridayError("invalid_plan", "Agent or role is not in the allowed catalog.")
        definition = catalog[agent]
        if not isinstance(definition, dict) or not isinstance(definition.get("vendor"), str) or not definition["vendor"].strip() or not isinstance(definition.get("model"), str) or not definition["model"].strip() or not isinstance(definition.get("roles"), list) or role not in definition["roles"]:
            raise FridayError("invalid_profile", "Agent must declare vendor, model and permitted roles.")
        if not isinstance(item["brief"], str) or not item["brief"].strip():
            raise FridayError("invalid_plan", "Agent assignment requires a brief.")
        if not isinstance(item["skills"], list) or any(not isinstance(s, str) or s not in skills for s in item["skills"]):
            raise FridayError("invalid_plan", "Agent assignment references an unknown skill.")
        if not isinstance(item["depends_on"], list) or any(not isinstance(dep, str) for dep in item["depends_on"]):
            raise FridayError("invalid_plan", "Agent dependencies must be a list of IDs.")
        assignments[name] = dict(item)
        vendor = definition["vendor"].strip().casefold()
        vendors[name] = {"claude": "anthropic", "codex": "openai", "z.ai": "zai"}.get(vendor, vendor)
    visiting, visited = set(), set()
    def visit(name):
        if name not in assignments or name in visiting:
            raise FridayError("invalid_plan", "Agent dependencies contain a cycle or unknown ID.")
        if name in visited:
            return
        visiting.add(name)
        for dep in assignments[name]["depends_on"]:
            visit(dep)
        visiting.remove(name)
        visited.add(name)
    for name in assignments:
        visit(name)
    developers = {vendors[name] for name, item in assignments.items() if item["role"] == "develop"}
    if developers and any(vendors[name] in developers for name, item in assignments.items() if item["role"] == "review"):
        raise FridayError("reviewer_not_independent", "Review must use a different vendor from development.")
    developer_ids = {name for name, item in assignments.items() if item["role"] == "develop"}
    def ancestors(name):
        result = set(assignments[name]["depends_on"])
        for dependency in assignments[name]["depends_on"]:
            result.update(ancestors(dependency))
        return result
    for name, item in assignments.items():
        if item["role"] in ("review", "blind-code", "blind-behavior") and not developer_ids.issubset(ancestors(name)):
            raise FridayError("invalid_plan", "Reviews must depend on all development assignments.")
    return list(assignments.values())
