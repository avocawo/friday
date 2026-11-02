import hashlib
from pathlib import Path

from . import criteria, gate, profile, state
from .errors import FridayError
from .locks import locked


def add(root, key, name):
    state.validate_key(name)
    def change(card):
        if name in card["stages"]:
            raise FridayError("already_exists", "Stage already exists: " + name)
        card["stages"][name] = {"status": "open"}
        card["events"].append({"type": "stage_opened", "stage": name, "at": state.now()})
    return state.update(root, key, change)


def close(root, key, name, method, value=None):
    initial = state.read(root, key)
    selected = profile.load(initial["profile"])
    worktree = initial.get("worktree", selected["repository"])
    def change(card):
        current_profile = profile.load(card["profile"])
        if card.get("worktree", current_profile["repository"]) != worktree:
            raise FridayError("worktree_conflict", "The worktree changed before closing the stage.")
        stage = card["stages"].get(name)
        if not stage or stage.get("status") != "open":
            raise FridayError("invalid_stage", "Stage is not open: " + name)
        prior = stage.get("gate", {})
        if (prior.get("status") == "environment_unavailable" or stage.get("environment_lock")) and method != "owner":
            raise FridayError("owner_required", "An unavailable environment requires an owner decision.")
        if method == "gate":
            if prior.get("status") != "passed":
                raise FridayError("gate_required", "Stage has no successful isolated gate.")
            latest_gate = next((event for event in reversed(card['events']) if event.get('type') == 'gate' and event.get('stage') == name), None)
            if (prior.get('exit_code') != 0 or prior.get('unchanged') is not True or not latest_gate or
                    latest_gate.get('status') != 'passed' or latest_gate.get('record_sha256') != gate.record_digest(prior)):
                raise FridayError('stale_evidence', 'Gate evidence is missing or changed; run the gate again.')
            if prior.get("decision_revision", 0) != card.get("decision_revision", 0):
                raise FridayError("stale_evidence", "A decision changed since the gate ran.")
            if prior.get("plan_revision", 0) != card.get("plan_revision", 0):
                raise FridayError("stale_evidence", "The plan changed since the gate ran.")
            selected = profile.load(card["profile"])
            selected["repository"] = card.get("worktree", selected["repository"])
            selected = gate.with_baseline(selected, card)
            head, digest, _ = gate.snapshot(selected)
            image = gate.image_id(selected.get("gate_image", ""))
            if (prior["head"], prior["snapshot"], prior["definition"]) != (head, digest, gate.definition(selected, image)):
                raise FridayError("stale_evidence", "The code or gate has changed since the successful run.")
            if "criteria" in stage:
                for role in criteria.review_roles(stage):
                    runs = [event for event in card["events"] if event.get("type") == "model_run" and
                            event.get("stage") == name and event.get("role") == role and not event.get('fallback_for')]
                    if runs and runs[-1].get('verdict') == 'unverifiable' and role == 'blind-code':
                        runs = [event for event in card['events'] if event.get('type') == 'model_run' and event.get('stage') == name and
                                event.get('role') == 'review' and event.get('fallback_for') == role and
                                event.get('verified_criteria') == criteria.for_role(stage, role)]
                    if not runs or runs[-1].get("verdict") != "pass" or runs[-1].get("head") != head or runs[-1].get("decision_revision", 0) != card.get("decision_revision", 0) or runs[-1].get("plan_revision", 0) != card.get("plan_revision", 0):
                        raise FridayError("review_required", "A passing current-code review is required: " + role)
                    binding = runs[-1].get('behavior_evidence_binding')
                    if binding:
                        from .workflow import behavior_evidence
                        verified = behavior_evidence(root, key, card, name, stage, head)
                        if not verified or verified[1] != binding:
                            raise FridayError('stale_evidence', 'The browser evidence used by the review has changed.')
                    gate_binding = runs[-1].get('gate_evidence_binding')
                    if gate_binding:
                        actual = gate.record_digest(prior)
                        if gate_binding != actual or not latest_gate or latest_gate.get('record_sha256') != actual or latest_gate.get('status') != 'passed':
                            raise FridayError('stale_evidence', 'The gate evidence used by the review has changed.')
            evidence = "gate: " + prior["snapshot"]
        elif method == "document":
            path = Path(value).expanduser().resolve()
            try:
                content = path.read_bytes()
                lines = content.decode("utf-8").splitlines()
            except (OSError, UnicodeError) as exc:
                raise FridayError("invalid_evidence", str(exc))
            if next((line.strip() for line in reversed(lines) if line.strip()), "") != "VERDICT: pass":
                raise FridayError("invalid_evidence", "The last non-empty line must be VERDICT: pass.")
            evidence = "%s sha256:%s" % (path, hashlib.sha256(content).hexdigest())
        elif method == "owner" and value and value.strip():
            evidence = "owner: " + value.strip()
        else:
            raise FridayError("invalid_evidence", "A closing method and its evidence are required.")
        stage.update({"status": "closed", "evidence": evidence, "closed_at": state.now()})
        card["events"].append({"type": "stage_closed", "stage": name, "method": method, "at": state.now()})
    with locked(root, "resource:branch:" + worktree, timeout=1800):
        return state.update(root, key, change)
