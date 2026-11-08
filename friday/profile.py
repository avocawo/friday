"""Explicit JSON project profiles, with paths resolved relative to the profile."""
import hashlib
import json
import re
from pathlib import Path

from . import design, models, steps, stories
from .errors import FridayError


def policy_fingerprint(selected):
    return hashlib.sha256(json.dumps(selected.get("engineering_policy", []), ensure_ascii=False).encode()).hexdigest()


def verify_policy(selected, card):
    recorded = card.get("plan_policy")
    if (recorded is None and selected.get("engineering_policy")) or (recorded is not None and recorded != policy_fingerprint(selected)):
        raise FridayError("stale_evidence", "Project engineering policy differs from the plan; regenerate the plan before continuing.")


def merge(base, override):
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def positive_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def execution_limits(selected):
    """Validate optional runtime and task limits without changing defaults of existing profiles."""
    models.executor_tiers(selected)
    if "max_parallel_milestones" in selected and not positive_integer(selected["max_parallel_milestones"]):
        raise FridayError("invalid_profile", "max_parallel_milestones must be a positive integer.")
    if "budget" in selected:
        budget = selected["budget"]
        if (not isinstance(budget, dict) or set(budget) - {"max_model_runs", "max_milestone_runs"}
                or any(not positive_integer(value) for value in budget.values())):
            raise FridayError("invalid_profile", "budget supports positive integer max_model_runs and max_milestone_runs.")
    if "final_review_roles" in selected and selected["final_review_roles"] not in (
            ["final-review"], list(models.FINAL_REVIEW_ROLES)):
        raise FridayError("invalid_profile", "final_review_roles must be ['final-review'] or ['final-review', 'blind-code'].")
    if "limits" in selected:
        limits = selected["limits"]
        if not isinstance(limits, dict):
            raise FridayError("invalid_profile", "limits must be an object.")
        for key in ("stage_files", "stage_lines", "task_milestones", "task_stages"):
            if key in limits and not positive_integer(limits[key]):
                raise FridayError("invalid_profile", "limits.%s must be a positive integer." % key)


def read_layers(path, seen):
    if path in seen:
        raise FridayError("invalid_profile", "Profile inheritance contains a cycle: " + str(path))
    try:
        layer = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FridayError("invalid_profile", "Cannot load profile %s: %s" % (path, exc))
    if not isinstance(layer, dict):
        raise FridayError("invalid_profile", "Profile must contain a JSON object.")
    parent = layer.pop("extends", None)
    showcase = layer.get("stories")
    if isinstance(showcase, dict):
        for field in ("repository", "worktree_root"):
            if isinstance(showcase.get(field), str) and showcase[field]:
                showcase[field] = str((path.parent / Path(showcase[field]).expanduser()).resolve())
    steps.resolve_paths(layer, path.parent)
    if parent is not None and (not isinstance(parent, str) or not parent):
        raise FridayError("invalid_profile", "extends must be a non-empty profile path.")
    for field in ("repository", "journal_dir", "worktree_root", "dor_file"):
        if field in layer:
            if not isinstance(layer[field], str) or not layer[field]:
                raise FridayError("invalid_profile", field + " must be a non-empty path.")
            layer[field] = str((path.parent / layer[field]).resolve())
    if not parent:
        return layer
    parent_path = (path.parent / Path(parent).expanduser()).resolve()
    return merge(read_layers(parent_path, seen | {path}), layer)


def load(path):
    path = Path(path).expanduser().resolve()
    profile = read_layers(path, set())
    if not isinstance(profile.get("name"), str) or not profile["name"]:
        raise FridayError("invalid_profile", "Profile requires a non-empty name.")
    if not isinstance(profile.get("repository"), str) or not profile["repository"]:
        raise FridayError("invalid_profile", "Profile requires a repository path.")
    gate = profile.get("gate")
    if gate is not None and (not isinstance(gate, list) or not gate or any(not isinstance(v, str) or not v for v in gate)):
        raise FridayError("invalid_profile", "gate must be a non-empty array of arguments.")
    patterns = profile.get("environment_errors", [])
    if not isinstance(patterns, list):
        raise FridayError("invalid_profile", "environment_errors must be an array of patterns.")
    for pattern in patterns:
        try:
            re.compile(pattern)
        except (re.error, TypeError) as exc:
            raise FridayError("invalid_profile", "Invalid environment error pattern: %s" % exc)
    policy = profile.get("engineering_policy", [])
    if not isinstance(policy, list) or any(not isinstance(item, str) or not item.strip() for item in policy):
        raise FridayError("invalid_profile", "engineering_policy must be an array of non-empty instructions.")
    docs = profile.get("rules_docs", [])
    if not isinstance(docs, list) or any(not isinstance(item, str) or not item or item.startswith("/") or ".." in item.split("/")
                                         for item in docs):
        raise FridayError("invalid_profile", "rules_docs must be an array of patterns relative to the repository.")
    if profile.get("repo_map", "none") not in ("none", "builtin", "graphify"):
        raise FridayError("invalid_profile", "repo_map must be none, builtin or graphify.")
    budget = profile.get("repo_map_budget", 60000)
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1000:
        raise FridayError("invalid_profile", "repo_map_budget must be a number of characters, at least 1000.")
    if profile.get("repo_map_mcp") not in (None, True, False) or (profile.get("repo_map_mcp") and profile.get("repo_map") != "graphify"):
        raise FridayError("invalid_profile", "repo_map_mcp is a boolean and needs repo_map graphify.")
    if profile.get("approval", "owner") not in ("owner", "auto"):
        raise FridayError("invalid_profile", "approval must be owner or auto.")
    execution_limits(profile)
    design.validate(profile.get("design"))
    stories.validate(profile.get("stories"))
    steps.validate(profile.get("steps"))
    if isinstance(profile.get("preview"), dict) and profile["preview"].get("backend") == "storybook":
        from . import isolated_preview
        isolated_preview.validate_storybook(profile["preview"])
    profile["path"] = str(path)
    return profile
