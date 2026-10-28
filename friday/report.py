"""Summarize acceptance evidence without equating an owner's word with a gate."""
from . import gate, profile, state
from .errors import FridayError


def acceptance(root, key):
    card = state.read(root, key)
    selected = profile.load(card["profile"])
    worktree = card.get("worktree", selected["repository"])
    head = gate.git(worktree, "rev-parse", "HEAD").decode().strip()
    dirty = bool(gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"))
    current_gate = None
    try:
        selected["repository"] = worktree
        if selected.get("gate") and selected.get("gate_image"):
            selected = gate.with_baseline(selected, card)
            current_head, snapshot, _ = gate.snapshot(selected)
            image = gate.image_id(selected["gate_image"])
            current_gate = (current_head, snapshot, gate.definition(selected, image))
    except FridayError:
        pass
    def matches(record):
        return current_gate is not None and record.get("status") == "passed" and (
            record.get("head"), record.get("snapshot"), record.get("definition")) == current_gate and record.get("decision_revision", 0) == card.get("decision_revision", 0) and record.get("plan_revision", 0) == card.get("plan_revision", 0)
    rows = []
    for name, stage in card["stages"].items():
        evidence = stage.get("evidence", "")
        gate_record = stage.get("gate", {})
        if stage.get("status") != "closed":
            verdict = "open"
        elif evidence.startswith("gate:") and matches(gate_record) and not stage.get("environment_lock") and not dirty:
            verdict = "gate_passed"
        elif evidence.startswith("owner:"):
            verdict = "owner_accepted"
        elif evidence and not evidence.startswith("gate:"):
            verdict = "document_accepted"
        else:
            verdict = "stale"
        for criterion in stage.get("criteria", []):
            rows.append({"stage": name, "criterion": criterion, "status": verdict, "evidence": evidence})
    final = card["stages"].get("final", {})
    final_gate = final.get("gate", {})
    return {"head": head, "dirty": dirty, "criteria": rows,
            "final_gate_passed": final.get("status") == "closed" and str(final.get("evidence", "")).startswith("gate:") and
                                 not final.get("environment_lock") and matches(final_gate) and not dirty}
